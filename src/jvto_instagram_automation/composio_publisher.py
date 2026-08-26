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

    def publish_carousel(self, image_urls: list[str], caption: str, instagram_user_id: str | None = None) -> dict[str, Any]:
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

        container_payload = {'ig_user_id': instagram_user_id, 'caption': caption, 'children': child_creation_ids}
        try:
            # INSTAGRAM_CREATE_CAROUSEL_CONTAINER is the confirmed real Composio
            # action name; the lowercase ones are kept only as a defensive
            # fallback for older toolkit aliases.
            container = self._invoke_tool(
                tools,
                ('INSTAGRAM_CREATE_CAROUSEL_CONTAINER', 'instagram_create_carousel_container', 'instagram_create_container'),
                container_payload,
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

        return {'status': 'published', 'data': publish_result, 'creation_id': creation_id}
