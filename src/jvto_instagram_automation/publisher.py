from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import requests


def upload_to_imgbb(image_path: Path, api_key: str | None) -> str | None:
    if not api_key:
        return None
    with image_path.open('rb') as handle:
        encoded = base64.b64encode(handle.read()).decode('ascii')
    response = requests.post('https://api.imgbb.com/1/upload', data={'key': api_key, 'image': encoded}, timeout=60)
    response.raise_for_status()
    return response.json().get('data', {}).get('url')


def publish_to_instagram(image_url: str, caption: str, access_token: str | None, instagram_user_id: str | None) -> dict[str, Any]:
    if not access_token or not instagram_user_id:
        return {'status': 'dry_run', 'message': 'Set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID to publish to Instagram.'}
    try:
        media_response = requests.post(
            f'https://graph.facebook.com/v20.0/{instagram_user_id}/media',
            params={'access_token': access_token, 'image_url': image_url, 'caption': caption},
            timeout=60,
        )
        media_response.raise_for_status()
        media_id = media_response.json().get('id')
        publish_response = requests.post(
            f'https://graph.facebook.com/v20.0/{instagram_user_id}/media_publish',
            params={'access_token': access_token, 'creation_id': media_id},
            timeout=60,
        )
        publish_response.raise_for_status()
        return {'status': 'published', 'message': publish_response.text}
    except Exception as exc:
        return {'status': 'error', 'message': str(exc)}
