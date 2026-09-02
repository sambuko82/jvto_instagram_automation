from __future__ import annotations

import argparse
import time
from pathlib import Path

from .composio_publisher import ComposioPublisher
from .facebook_publisher import FacebookPublisher
from .config import load_settings
from .drive_ingestion import DriveIngestion
from .publisher import publish_to_instagram, upload_to_imgbb
from .rendering import create_carousel
from .review_parser import build_caption, load_review_payload, record_posted_review_from_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate reusable JVTO carousel cards')
    parser.add_argument('--local-json', default=None)
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--imgbb-key', default=None)
    parser.add_argument('--instagram-token', default=None)
    parser.add_argument('--instagram-user-id', default=None)
    parser.add_argument('--drive-export', action='store_true')
    parser.add_argument('--agentic', action='store_true', help='Use LLM-based review-to-caption extraction when credentials are available')
    parser.add_argument('--post-trip', action='store_true', help='Publish the next pending trip photo row from the spreadsheet')
    parser.add_argument('--force', action='store_true', help='Ignore the interval gate and post now')
    parser.add_argument('--booking-id', default=None,
                        help='Publish this booking instead of the next queued one')
    return parser


def _mark_uploaded_with_retry(queue, row, now, platform: str = 'instagram',
                              attempts: int = 3, backoff_seconds: float = 1.0) -> bool:
    """Retries `queue.mark_uploaded` a bounded number of times.

    This only runs after `publish_carousel` has already succeeded. If the
    BATCH_UPDATE that records that keeps failing, the trip stays 'pending' in
    the sheet and the next cron run would post the same carousel to the real
    Instagram account a second time - unlike every other failure in this
    command, that one isn't fixed by just re-running. A transient blip
    (network error, Composio 5xx) is the common case, so a short bounded
    retry turns it into a non-event; only the marking is retried, never the
    publish itself.
    """
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            queue.mark_uploaded(row, now, platform)
            return True
        except Exception as exc:  # noqa: BLE001 - any failure here is retried, then reported
            last_exc = exc
            if attempt < attempts:
                time.sleep(backoff_seconds)

    print(
        f'{row.booking_id} WAS PUBLISHED to {platform}, but marking sheet row {row.row_number} '
        f'as uploaded failed {attempts} times in a row: {last_exc}. '
        f'Set row {row.row_number}\'s "Is Uploaded {"IG" if platform == "instagram" else "FB"}" '
        f'to TRUE by hand before the next run, or this trip will be posted to {platform} again.'
    )
    return False


def run_post_trip(settings, force: bool, queue=None, publisher=None, fb_publisher=None,
                  booking_id: str | None = None) -> int:
    from datetime import datetime, timezone

    from .sheet_queue import (SheetQueue, _is_postable, days_since_last_upload,
                              next_pending, next_unfinished)

    if not settings.composio_api_key or not settings.trip_photo_spreadsheet_id:
        print('COMPOSIO_API_KEY and TRIP_PHOTO_SPREADSHEET_ID are both required for --post-trip.')
        return 1

    if queue is None:
        queue = SheetQueue(
            settings.composio_api_key,
            settings.composio_user_id or 'jvto_automation',
            settings.trip_photo_spreadsheet_id,
            settings.trip_photo_sheet_name,
        )

    rows = queue.fetch_rows()
    now = datetime.now(timezone.utc)

    # A named booking is an operator decision, so it overrides both the queue
    # order and the interval gate. It still has to pass the same checks the
    # queue applies - being chosen by hand does not make a broken row postable.
    if booking_id:
        row = next((r for r in rows if r.booking_id == booking_id), None)
        if row is None:
            print(f'{booking_id} is not in the sheet. Nothing to do.')
            return 1
        if row.is_uploaded:
            print(f'{booking_id} is already published on both platforms. Nothing to do.')
            return 0
        if not _is_postable(row):
            return 1
        print(f'Publishing {row.booking_id} ({len(row.photo_urls)} photos) from sheet row {row.row_number}')
        return _publish(queue, row, now, settings, publisher, fb_publisher)

    # A trip stranded on one platform is a repair, not a new post, so it is
    # finished before the schedule is consulted. Otherwise a Facebook failure
    # would wait four days for the gate to reopen while the Instagram half sat
    # public and unmatched.
    row = next_unfinished(rows)

    if row is None:
        # The cron runs daily and this gate enforces the real spacing, so a run
        # lost to an outage is picked up the next day instead of slipping a
        # full cycle.
        elapsed = days_since_last_upload(rows, now)
        if not force and elapsed is not None and elapsed < settings.trip_post_interval_days:
            print(f'Last post was {elapsed:.1f} days ago; waiting for {settings.trip_post_interval_days}. Nothing to do.')
            return 0

        row = next_pending(rows)

    if row is None:
        print('No pending trip photo rows. Nothing to do.')
        return 0

    print(f'Publishing {row.booking_id} ({len(row.photo_urls)} photos) from sheet row {row.row_number}')

    if publisher is None:
        publisher = ComposioPublisher(settings.composio_api_key, settings.composio_user_id)
    if fb_publisher is None:
        fb_publisher = FacebookPublisher(settings.composio_api_key, settings.composio_user_id)

    return _publish(queue, row, now, settings, publisher, fb_publisher)


def _publish(queue, row, now, settings, publisher, fb_publisher) -> int:
    failures = 0

    # Independent on purpose. Instagram carries the product tag and the crew
    # credit; Facebook carries the clickable link neither of those replaces. A
    # trip losing one is not a reason to withhold the other, and whichever
    # failed is picked up by the unfinished scan on the next run.
    if not row.is_uploaded_ig:
        failures += _post_instagram(queue, row, now, settings, publisher)

    if not row.is_uploaded_fb:
        failures += _post_facebook(queue, row, now, settings, fb_publisher)

    return 1 if failures else 0


def _post_instagram(queue, row, now, settings, publisher) -> int:
    result = publisher.publish_carousel(
        row.photo_urls,
        row.caption,
        settings.instagram_user_id,
        collaborators=row.instagram_usernames,
        package_code=row.package_code,
    )

    if result.get('status') != 'published':
        print(f"Instagram publish failed: {result.get('status')} - {result.get('message')}")
        return 1

    if not _mark_uploaded_with_retry(queue, row, now, 'instagram'):
        return 1

    tagged = [u for u in row.instagram_usernames if u not in result.get('dropped_collaborators', [])]
    credit = f' Collaborators tagged: {", ".join(tagged)}.' if tagged else ''

    if result.get('product_tagged'):
        product = f' Product tagged: {row.package_code}.'
    elif row.package_code:
        product = f" Product NOT tagged ({row.package_code}): {result.get('product_tag_skipped')}."
    else:
        product = ''

    print(f'Instagram: published {row.booking_id}, row {row.row_number} marked.{credit}{product}')
    return 0


def _post_facebook(queue, row, now, settings, publisher) -> int:
    # The caption goes out whole. Instagram drops its trailing link once a
    # product tag carries it, but on Facebook that link is clickable and is the
    # only thing that will actually take a reader to the package page.
    result = publisher.publish_photo_post(
        row.photo_urls,
        row.caption,
        page_name=settings.facebook_page_name,
    )

    if result.get('status') != 'published':
        print(f"Facebook publish failed: {result.get('status')} - {result.get('message')}")
        return 1

    if not _mark_uploaded_with_retry(queue, row, now, 'facebook'):
        return 1

    print(f"Facebook: published {row.booking_id} as {result.get('post_id')}, row {row.row_number} marked.")
    return 0


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.local_json:
        settings.local_json = Path(args.local_json).resolve()
    if args.output_dir:
        settings.output_dir = Path(args.output_dir).resolve()
    if args.publish:
        settings.publish = True
    if args.agentic:
        settings.agentic = True
    if args.imgbb_key:
        settings.imgbb_api_key = args.imgbb_key
    if args.instagram_token:
        settings.instagram_access_token = args.instagram_token
    if args.instagram_user_id:
        settings.instagram_user_id = args.instagram_user_id

    if args.post_trip:
        return run_post_trip(settings, args.force, booking_id=args.booking_id)

    if args.drive_export:
        ingestion = DriveIngestion(settings.drive_folder_id, settings.composio_api_key, settings.composio_user_id, settings.project_root)
        if not ingestion.is_configured():
            print(
                "COMPOSIO_API_KEY is not set - skipping Drive export. "
                "Run 'composio link googledrive' once, then set COMPOSIO_API_KEY, to enable this."
            )
        else:
            try:
                exported = ingestion.export_reviews_to_json(settings.project_root / 'data' / 'drive_reviews.json')
            except Exception as exc:
                print(f'Drive export failed: {exc}. Falling back to local sample input if available.')
                exported = []
            if exported:
                print(f'Exported {len(exported)} review(s) from Drive to data/drive_reviews.json')
                settings.local_json = settings.project_root / 'data' / 'drive_reviews.json'
            else:
                print('No reviews were returned from Drive. Falling back to the local sample input if available.')

    try:
        payload = load_review_payload(settings)
    except ValueError as exc:
        print(f'Error: {exc}')
        return 1

    card_paths = create_carousel(payload, settings.output_dir)
    print(f'Generated {len(card_paths)} cards in {settings.output_dir}')
    for card_path in card_paths:
        print(card_path)

    if settings.publish:
        caption = build_caption(payload)

        image_urls: list[str] = []
        if settings.imgbb_api_key:
            for card_path in card_paths:
                try:
                    url = upload_to_imgbb(card_path, settings.imgbb_api_key)
                except Exception as exc:
                    print(f'ImgBB upload failed for {card_path}: {exc}')
                    url = None
                if url:
                    image_urls.append(url)

        publish_result = None
        if settings.composio_api_key and len(image_urls) == len(card_paths):
            publisher = ComposioPublisher(settings.composio_api_key, settings.composio_user_id, settings.project_root)
            publish_result = publisher.publish_carousel(image_urls, caption, settings.instagram_user_id)
            print({'composio_publish': publish_result})

        needs_manual_fallback = publish_result is None or publish_result.get('status') in {'missing_api_key', 'not_authorized', 'error'}
        manual_result = None
        if needs_manual_fallback:
            if settings.instagram_access_token and settings.instagram_user_id and image_urls:
                manual_result = publish_to_instagram(image_urls[-1], caption, settings.instagram_access_token, settings.instagram_user_id)
                print({'manual_publish': manual_result})
            elif publish_result is None:
                print('Instagram publishing skipped: no Composio API key, no ImgBB key, or uploads incomplete.')

        published = (publish_result or {}).get('status') == 'published' or (manual_result or {}).get('status') == 'published'
        if published:
            # So the next --publish run skips this review instead of picking
            # the same top-ranked one again.
            record_posted_review_from_payload(settings, payload)

    return 0
