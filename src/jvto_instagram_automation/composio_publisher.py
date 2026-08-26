from __future__ import annotations

import os
from pathlib import Path
from typing import Any


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
            composio = Composio(api_key=self.api_key)
            return composio.create(user_id=self.user_id, toolkits=['instagram'])
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
            session = self._get_session()
        except Exception as exc:  # pragma: no cover - optional dependency path
            return {'status': 'error', 'message': str(exc)}
        if session is None:
            return {
                'status': 'not_authorized',
                'message': "Composio SDK unavailable or Instagram not linked. Run 'composio link instagram' first.",
            }

        try:
            tools = session.tools()
        except Exception as exc:  # pragma: no cover - optional dependency path
            return {'status': 'error', 'message': str(exc)}

        container_payload = {'ig_user_id': instagram_user_id, 'caption': caption, 'child_image_urls': image_urls}
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
