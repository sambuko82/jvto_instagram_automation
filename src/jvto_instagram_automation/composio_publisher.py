from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class _ComposioToolExecutor:
    """Adapts the current Composio SDK (`client.tools.execute`) to the
    `.execute(tool_name, kwargs)` shape the rest of this module expects.

    The Composio SDK's `Composio(...).create(...).tools()` call now returns
    Tool Router meta-tool schemas (for LLM-driven agents) rather than
    directly callable per-action objects, so toolkit actions like
    INSTAGRAM_CREATE_CAROUSEL_CONTAINER have to be invoked through
    `client.tools.execute` instead.
    """

    def __init__(self, client: Any, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    def execute(self, tool_name: str, kwargs: dict[str, Any]) -> Any:
        response = self._client.tools.execute(
            tool_name,
            kwargs,
            user_id=self._user_id,
            dangerously_skip_version_check=True,
        )
        if isinstance(response, dict):
            if response.get('successful') is False:
                raise RuntimeError(response.get('error') or f'{tool_name} failed')
            if 'data' in response:
                return response['data']
        return response


class ComposioPublisher:
    """Publishes carousels to Instagram via Composio.

    Auth is handled entirely by the Composio CLI (`composio link instagram`)
    ahead of time - no local auth-state file is written here. See
    drive_ingestion.ComposioConnector for why the old DIY state-file approach
    was removed rather than patched.
    """

    def __init__(self, api_key: str | None = None, user_id: str | None = None, project_root: Path | None = None) -> None:
        self.api_key = api_key or os.getenv('COMPOSIO_API_KEY')
        self.user_id = user_id or os.getenv('COMPOSIO_USER_ID') or 'jvto_automation'
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2])

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get_session(self) -> Any | None:
        if not self.api_key:
            return None
        try:
            from composio import Composio
        except Exception:
            return None

        try:
            client = Composio(api_key=self.api_key)
            return _ComposioToolExecutor(client, self.user_id)
        except Exception:
            return None

    def _invoke_tool(self, tools: Any, tool_names: tuple[str, ...], kwargs: dict[str, Any]) -> Any:
        if tools is None:
            raise RuntimeError('Composio tools are not available')

        for tool_name in tool_names:
            for method_name in ('execute', 'run', 'invoke'):
                method = getattr(tools, method_name, None)
                if not callable(method):
                    continue
                try:
                    return method(tool_name, kwargs)
                except TypeError:
                    try:
                        return method(tool_name, **kwargs)
                    except Exception:
                        continue
                except Exception:
                    continue
        raise RuntimeError('No compatible Instagram tool call succeeded')


    def _connected_account_id(self) -> str | None:
        """The Instagram account linked under this user id.

        Looked up rather than configured: the id belongs to the connection, not
        to this deployment, and hard-coding it would break silently the first
        time the account is relinked.
        """
        from composio import Composio

        client = Composio(api_key=self.api_key).client
        page = client.connected_accounts.list(user_ids=[self.user_id])

        for account in getattr(page, 'items', []) or []:
            toolkit = getattr(account, 'toolkit', None)
            slug = getattr(toolkit, 'slug', None) or (toolkit or {}).get('slug') if toolkit else None
            if slug == 'instagram' and getattr(account, 'status', None) == 'ACTIVE':
                return getattr(account, 'id', None)

        return None

    # Meta answers this when it cannot resolve a collaborator, and it means the
    # username is wrong or the account is private. It groups both causes, so the
    # only safe response is to drop the tags and still publish.
    _COLLABORATOR_REJECTED = 'private profile or invalid'

    def _create_carousel_container(
        self, instagram_user_id: str | None, caption: str, children: list[str], collaborators: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        """Create the parent container, giving up the collaborator tags before
        giving up the post.

        A crew member who renamed their Instagram account should cost the trip
        its collaborator credit, not its entire post.
        """
        import json

        from composio import Composio

        account_id = self._connected_account_id()
        if not account_id:
            raise RuntimeError('No ACTIVE Instagram connection found for this user id')

        client = Composio(api_key=self.api_key).client

        def attempt(tags: list[str]) -> dict[str, Any]:
            body: dict[str, Any] = {
                'media_type': 'CAROUSEL',
                'children': ','.join(children),
                'caption': caption,
            }
            if tags:
                body['collaborators'] = json.dumps(tags)

            response = client.tools.proxy(
                endpoint=f'/{instagram_user_id}/media',
                method='POST',
                body=body,
                connected_account_id=account_id,
            )
            data = response.data if isinstance(response.data, dict) else {}

            if 'id' not in data:
                message = (data.get('error') or {}).get('message', str(data))
                raise RuntimeError(message)

            return data

        if not collaborators:
            return attempt([]), []

        try:
            return attempt(collaborators), []
        except RuntimeError as exc:
            if self._COLLABORATOR_REJECTED not in str(exc):
                raise

            print(
                f'Instagram rejected the collaborator tags {collaborators}: the usernames are '
                'wrong or those accounts are private. Publishing without them - fix the '
                'instagram_username values so the next post credits them.'
            )

            return attempt([]), list(collaborators)

    def publish_carousel(
        self,
        image_urls: list[str],
        caption: str,
        instagram_user_id: str | None = None,
        collaborators: list[str] | None = None,
    ) -> dict[str, Any]:
        if not image_urls:
            return {'status': 'dry_run', 'message': 'No image URLs were supplied for carousel publishing.'}
        if not self.is_configured():
            return {'status': 'missing_api_key', 'message': 'COMPOSIO_API_KEY is not configured'}

        try:
            tools = self._get_session()
        except Exception as exc:  # pragma: no cover - optional dependency path
            return {'status': 'error', 'message': str(exc)}
        if tools is None:
            return {
                'status': 'not_authorized',
                'message': "Composio SDK unavailable or Instagram not linked. Run 'composio link instagram' first.",
            }

        child_creation_ids: list[str] = []
        for image_url in image_urls:
            child_payload = {'ig_user_id': instagram_user_id, 'image_url': image_url, 'is_carousel_item': True}
            try:
                # INSTAGRAM_CREATE_MEDIA_CONTAINER is the confirmed real Composio
                # action name for creating a single carousel-child container -
                # INSTAGRAM_CREATE_CAROUSEL_CONTAINER only accepts already-created
                # child creation_ids, not raw image URLs.
                child_container = self._invoke_tool(
                    tools,
                    ('INSTAGRAM_CREATE_MEDIA_CONTAINER', 'instagram_create_media_container'),
                    child_payload,
                )
            except Exception as exc:
                return {'status': 'error', 'message': f'Failed to create carousel child container for {image_url}: {exc}'}

            child_id = child_container.get('id') if isinstance(child_container, dict) else getattr(child_container, 'id', None)
            if not child_id:
                return {'status': 'error', 'message': f'Child container created for {image_url} but no id was returned.', 'data': child_container}
            child_creation_ids.append(child_id)

        # The carousel container goes through Composio's HTTP proxy rather than
        # its Instagram tool, because the tool accepts only caption/children and
        # collaborator tagging needs a parameter it does not expose. The proxy
        # still uses the same managed connection, so no Meta token is handled
        # here.
        try:
            container, dropped = self._create_carousel_container(
                instagram_user_id, caption, child_creation_ids, collaborators or []
            )
        except Exception as exc:
            return {'status': 'error', 'message': f'Failed to create carousel container: {exc}'}

        creation_id = container.get('id') if isinstance(container, dict) else getattr(container, 'id', None)
        if not creation_id:
            return {'status': 'error', 'message': 'Carousel container created but no creation_id was returned.', 'data': container}

        publish_payload = {'ig_user_id': instagram_user_id, 'creation_id': creation_id}
        try:
            publish_result = self._invoke_tool(
                tools,
                ('INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH', 'instagram_publish_media', 'instagram_post'),
                publish_payload,
            )
        except Exception as exc:
            return {'status': 'error', 'message': f'Carousel container created but publish failed: {exc}', 'creation_id': creation_id}

        return {
            'status': 'published',
            'data': publish_result,
            'creation_id': creation_id,
            'dropped_collaborators': dropped,
        }
