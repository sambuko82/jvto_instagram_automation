from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class InstagramAuthState:
    session_id: str | None = None
    connection_request_id: str | None = None
    redirect_url: str | None = None
    status: str = 'pending'


class ComposioPublisher:
    def __init__(self, api_key: str | None = None, user_id: str | None = None, project_root: Path | None = None) -> None:
        self.api_key = api_key or os.getenv('COMPOSIO_API_KEY')
        self.user_id = user_id or os.getenv('COMPOSIO_USER_ID')
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2])
        self.state_path = self.project_root / '.instagram_auth.json'

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def load_state(self) -> InstagramAuthState | None:
        if not self.state_path.exists():
            return None
        payload = json.loads(self.state_path.read_text(encoding='utf-8'))
        return InstagramAuthState(**payload)

    def save_state(self, state: InstagramAuthState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(asdict(state), indent=2), encoding='utf-8')

    def _get_session(self) -> Any | None:
        if not self.api_key:
            return None

        try:
            from composio import Composio
        except Exception:
            return None

        auth_state = self.load_state()
        if not auth_state or not auth_state.session_id:
            return None

        composio = Composio(api_key=self.api_key)
        try:
            return composio.use(auth_state.session_id)
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

    def start_auth_flow(self) -> dict[str, Any]:
        if not self.api_key:
            return {'status': 'missing_api_key', 'message': 'Set COMPOSIO_API_KEY to start an Instagram auth flow.'}

        try:
            from composio import Composio
        except Exception as exc:  # pragma: no cover - optional dependency path
            return {'status': 'unavailable', 'message': f'Composio SDK is not available: {exc}'}

        composio = Composio(api_key=self.api_key)
        session = composio.create(user_id=self.user_id or 'jvto_automation', toolkits=['instagram'])
        connection_request = session.authorize('instagram')

        state = InstagramAuthState(
            session_id=getattr(session, 'session_id', None),
            connection_request_id=getattr(connection_request, 'id', None),
            redirect_url=getattr(connection_request, 'redirect_url', None),
            status=getattr(connection_request, 'status', 'pending'),
        )
        self.save_state(state)

        return {
            'status': 'auth_started',
            'message': 'Open the redirect URL to authorize the Instagram connection.',
            'redirect_url': state.redirect_url,
            'session_id': state.session_id,
            'connection_request_id': state.connection_request_id,
        }

    def publish_image(self, image_url: str, caption: str, instagram_user_id: str | None = None) -> dict[str, Any]:
        if not image_url:
            return {'status': 'dry_run', 'message': 'No image URL was supplied for publishing.'}
        if not self.is_configured():
            return {'status': 'missing_api_key', 'message': 'COMPOSIO_API_KEY is not configured'}

        session = self._get_session()
        if session is None:
            auth_flow = self.start_auth_flow()
            if auth_flow.get('status') == 'auth_started':
                return {'status': 'not_authorized', 'message': 'Complete the Composio Instagram authorization flow and retry.', 'auth': auth_flow}
            return auth_flow

        try:
            tools = session.tools()
        except Exception as exc:  # pragma: no cover - optional dependency path
            return {'status': 'error', 'message': str(exc)}

        payload = {'caption': caption, 'image_url': image_url, 'ig_user_id': instagram_user_id}
        for tool_name in (
            'instagram_create_media',
            'instagram_create_container',
            'instagram_create_carousel_container',
            'instagram_publish_media',
            'instagram_post',
        ):
            try:
                result = self._invoke_tool(tools, (tool_name,), payload)
                return {'status': 'published', 'data': result}
            except Exception:
                continue

        return {'status': 'unavailable', 'message': 'Composio did not expose a compatible Instagram publish tool in this environment.'}

    def publish_carousel(self, image_urls: list[str], caption: str, instagram_user_id: str | None = None) -> dict[str, Any]:
        if not image_urls:
            return {'status': 'dry_run', 'message': 'No image URLs were supplied for carousel publishing.'}
        if not self.is_configured():
            return {'status': 'missing_api_key', 'message': 'COMPOSIO_API_KEY is not configured'}

        session = self._get_session()
        if session is None:
            auth_flow = self.start_auth_flow()
            if auth_flow.get('status') == 'auth_started':
                return {'status': 'not_authorized', 'message': 'Complete the Composio Instagram authorization flow and retry.', 'auth': auth_flow}
            return auth_flow

        try:
            tools = session.tools()
        except Exception as exc:  # pragma: no cover - optional dependency path
            return {'status': 'error', 'message': str(exc)}

        payload = {'caption': caption, 'child_image_urls': image_urls, 'ig_user_id': instagram_user_id}
        for tool_name in ('instagram_create_carousel_container', 'instagram_create_container', 'instagram_create_media'):
            try:
                result = self._invoke_tool(tools, (tool_name,), payload)
                return {'status': 'published', 'data': result}
            except Exception:
                continue

        return {'status': 'unavailable', 'message': 'Composio did not expose a compatible carousel publish tool in this environment.'}
