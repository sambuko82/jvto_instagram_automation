from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_settings
from .drive_ingestion import DriveIngestion
from .publisher import publish_to_instagram, upload_to_imgbb
from .rendering import create_carousel
from .review_parser import load_review_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate reusable JVTO carousel cards')
    parser.add_argument('--local-json', default=None)
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--file-id', default=None)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--imgbb-key', default=None)
    parser.add_argument('--instagram-token', default=None)
    parser.add_argument('--instagram-user-id', default=None)
    parser.add_argument('--drive-export', action='store_true')
    parser.add_argument('--oauth', action='store_true', help='Launch the Composio-based Google Drive auth flow')
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
    if args.file_id:
        settings.file_id = args.file_id
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

    ingestion = DriveIngestion(
        settings.drive_folder_id,
        settings.drive_access_token,
        settings.composio_api_key,
        settings.composio_user_id,
        settings.project_root,
    )
    if args.oauth:
        result = ingestion.authorize()
        print(result.get('message', 'Composio auth flow started'))

    if args.drive_export:
        exported = ingestion.export_reviews_to_json(settings.project_root / 'data' / 'drive_reviews.json')
        if exported:
            print(f'Exported {len(exported)} review(s) from Drive to data/drive_reviews.json')
            settings.local_json = settings.project_root / 'data' / 'drive_reviews.json'
        else:
            print('No reviews were returned from the Composio/Drive workflow. Falling back to the local sample input if available.')

    payload = load_review_payload(settings)
    card_paths = create_carousel(payload, settings.output_dir)

    print(f'Generated {len(card_paths)} cards in {settings.output_dir}')
    for card_path in card_paths:
        print(card_path)

    if settings.publish:
        img_url = None
        if settings.imgbb_api_key:
            img_url = upload_to_imgbb(card_paths[-1], settings.imgbb_api_key)
        caption = f"{payload.narrative.guest_name} shares a JVTO story about {', '.join(payload.narrative.destinations)}."
        publish_result = publish_to_instagram(img_url or '', caption, settings.instagram_access_token, settings.instagram_user_id)
        print(publish_result)

    return 0
