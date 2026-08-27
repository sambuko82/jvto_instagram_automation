from __future__ import annotations

import argparse
from pathlib import Path

from .composio_publisher import ComposioPublisher
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
    return parser


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
