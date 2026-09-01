"""Publishes the same trip to the Facebook Page.

Facebook is not a lesser Instagram here, and the post is deliberately not a
copy. Two things Instagram does are simply absent: a Page post cannot carry a
catalog product tag - the Graph API accepts the parameter and discards it,
confirmed by reading a post back - and there is no collaborator equivalent, as
crew have Instagram handles rather than Pages.

One thing Facebook does that Instagram cannot: a link in the text is
clickable. On Instagram a caption URL is dead text, which is why the publisher
strips it once a product tag carries the link. On Facebook that same URL is
the only thing that will actually carry someone to the package page, so it
stays.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any


class FacebookPublisher:
    """Posts a photo set to a Page through Composio's HTTP proxy.

    The proxy is used rather than the FACEBOOK_* tools because a multi-photo
    post needs two steps Meta only exposes directly: photos uploaded
    unpublished, then one feed post that attaches them.
    """

    def __init__(self, api_key: str | None = None, user_id: str | None = None) -> None:
        import os

        self.api_key = api_key or os.getenv('COMPOSIO_API_KEY')
        self.user_id = user_id or os.getenv('COMPOSIO_USER_ID') or 'jvto_automation'

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _client(self) -> Any:
        from composio import Composio

        return Composio(api_key=self.api_key).client

    def _connected_account_id(self) -> str | None:
        """The Facebook connection on a custom auth config.

        The same filter the Instagram publisher uses for product tagging:
        Composio's managed Facebook OAuth is a different connection with
        different scopes, and posting must not silently land on whichever one
        happens to be listed first.
        """
        page = self._client().connected_accounts.list(user_ids=[self.user_id])

        for account in getattr(page, 'items', []) or []:
            toolkit = getattr(account, 'toolkit', None)
            if getattr(toolkit, 'slug', None) != 'facebook':
                continue
            if getattr(account, 'status', None) != 'ACTIVE':
                continue

            auth_config = getattr(account, 'auth_config', None)
            if getattr(auth_config, 'is_composio_managed', True):
                continue

            return getattr(account, 'id', None)

        return None

    def _call(self, account_id: str, endpoint: str, method: str = 'GET') -> dict[str, Any]:
        response = self._client().tools.proxy(
            endpoint=endpoint, method=method, connected_account_id=account_id
        )
        data = response.data if isinstance(response.data, dict) else {}

        if 'error' in data:
            raise RuntimeError((data['error'] or {}).get('message', json.dumps(data)[:200]))

        return data

    def _page(self, account_id: str, page_name: str | None) -> tuple[str, str]:
        """The target Page's id and its own access token.

        A Page post is authored by the Page, not by the person who authorised
        the app, so every call below carries the Page token rather than the
        user one. The account administers several Pages, so the right one is
        chosen by name and never by position.
        """
        pages = (self._call(account_id, '/me/accounts?fields=id,name,access_token') or {}).get('data') or []

        if not pages:
            raise RuntimeError('this connection administers no Facebook Pages')

        if page_name:
            for page in pages:
                if page.get('name') == page_name:
                    return page['id'], page['access_token']

            raise RuntimeError(f'no Page named {page_name!r} on this connection')

        return pages[0]['id'], pages[0]['access_token']

    def publish_photo_post(
        self,
        image_urls: list[str],
        message: str,
        page_name: str | None = None,
    ) -> dict[str, Any]:
        if not image_urls:
            return {'status': 'dry_run', 'message': 'No image URLs were supplied.'}
        if not self.is_configured():
            return {'status': 'missing_api_key', 'message': 'COMPOSIO_API_KEY is not configured'}

        try:
            account_id = self._connected_account_id()
            if not account_id:
                return {
                    'status': 'not_authorized',
                    'message': 'No Facebook connection on a custom auth config is linked.',
                }

            page_id, token = self._page(account_id, page_name)
        except Exception as exc:  # noqa: BLE001 - reported, never raised at the caller
            return {'status': 'error', 'message': str(exc)}

        # Uploaded unpublished, so nothing appears on the Page until the feed
        # post below attaches them all at once. A failure part-way leaves
        # orphans in the Page's photo library rather than a half-built post on
        # the timeline.
        media_ids: list[str] = []
        for image_url in image_urls:
            query = urllib.parse.urlencode({
                'url': image_url,
                'published': 'false',
                'access_token': token,
            })
            try:
                photo = self._call(account_id, f'/{page_id}/photos?{query}', 'POST')
            except Exception as exc:  # noqa: BLE001
                return {
                    'status': 'error',
                    'message': f'Failed to upload {image_url}: {exc}',
                    'orphaned_photo_ids': media_ids,
                }

            if not photo.get('id'):
                return {'status': 'error', 'message': f'No photo id returned for {image_url}'}

            media_ids.append(photo['id'])

        query = urllib.parse.urlencode(
            {'message': message, 'access_token': token}
            | {f'attached_media[{i}]': json.dumps({'media_fbid': mid}) for i, mid in enumerate(media_ids)}
        )
        try:
            post = self._call(account_id, f'/{page_id}/feed?{query}', 'POST')
        except Exception as exc:  # noqa: BLE001
            return {'status': 'error', 'message': f'Photos uploaded but the post failed: {exc}',
                    'orphaned_photo_ids': media_ids}

        return {'status': 'published', 'post_id': post.get('id'), 'photo_count': len(media_ids)}
