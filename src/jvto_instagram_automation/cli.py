from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_settings
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
    if args.imgbb_key:
        settings.imgbb_api_key = args.imgbb_key
    if args.instagram_token:
        settings.instagram_access_token = args.instagram_token
    if args.instagram_user_id:
        settings.instagram_user_id = args.instagram_user_id

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
