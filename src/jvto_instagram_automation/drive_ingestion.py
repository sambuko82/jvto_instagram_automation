from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests


class _ComposioToolExecutor:
    """Adapts the current Composio SDK (`client.tools.execute`) to the
    `.execute(tool_name, kwargs)` shape the rest of this module expects.

    The Composio SDK's `Composio(...).create(...).tools()` call now returns
    Tool Router meta-tool schemas (for LLM-driven agents) rather than
    directly callable per-action objects, so toolkit actions like
    GOOGLEDRIVE_LIST_FILES have to be invoked through `client.tools.execute`
    instead.
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


class ComposioConnector:
    """Reads JVTO review files from a Google Drive folder via Composio.

    Auth is handled entirely by the Composio CLI (`composio link googledrive`)
    ahead of time - there is no local auth-state file here on purpose. The
    original DIY .drive_auth.json approach was a secret-leak risk (it wasn't
    reliably gitignored) and duplicated what the Composio CLI already does
    correctly, so it has been removed rather than patched.
    """

    def __init__(
        self,
        api_key: str | None = None,
        user_id: str | None = None,
        project_root: Path | None = None,
    ) -> None:
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

    def _invoke_tool(self, tools: Any, names: tuple[str, ...], kwargs: dict[str, Any]) -> Any:
        if tools is None:
            raise RuntimeError('Composio tools are not available')

        for tool_name in names:
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
        raise RuntimeError('No compatible Google Drive tool call succeeded')

    def _coerce_file_items(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ('files', 'items', 'results', 'data'):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            if isinstance(payload.get('file'), dict):
                return [payload['file']]
        return []

    def _extract_content(self, payload: Any) -> str | None:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, bytes):
            return payload.decode('utf-8', errors='ignore')
        if isinstance(payload, dict):
            for key in ('content', 'data', 'text', 'body'):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
            if isinstance(payload.get('file'), dict):
                return self._extract_content(payload['file'])
        return None

    def _resolve_downloaded_content(self, payload: Any) -> str | None:
        # GOOGLEDRIVE_DOWNLOAD_FILE returns the bytes behind a short-lived
        # signed URL rather than inline content, so fetch it before falling
        # back to the generic inline-content shapes handled by
        # _extract_content.
        if isinstance(payload, dict):
            downloaded = payload.get('downloaded_file_content')
            if isinstance(downloaded, dict):
                s3url = downloaded.get('s3url') or downloaded.get('url')
                if s3url:
                    try:
                        response = requests.get(s3url, timeout=30)
                        response.raise_for_status()
                        return response.text
                    except Exception:
                        return None
        return self._extract_content(payload)

    def _looks_like_review_file(self, name: str | None) -> bool:
        if not name:
            return False
        lowered = name.lower()
        if lowered.endswith('.json'):
            return True
        if 'review' in lowered and (lowered.endswith('.csv') or lowered.endswith('.txt')):
            return True
        return False

    def read_review_files(self, folder_id: str | None = None, folder_name: str | None = None) -> list[dict[str, Any]]:
        tools = self._get_session()
        if tools is None:
            return []

        folder = folder_id or os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        list_payloads: tuple[dict[str, Any], ...]
        if folder:
            list_payloads = ({'folder_id': folder},)
        else:
            search_name = folder_name or os.getenv('GOOGLE_DRIVE_FOLDER', 'JVTO Reviews')
            list_payloads = ({'query': search_name}, {'folder_name': search_name}, {'name': search_name})

        file_items: list[dict[str, Any]] = []
        for payload in list_payloads:
            try:
                # GOOGLEDRIVE_LIST_FILES is the confirmed real Composio action
                # name; the lowercase ones are kept only as a defensive fallback
                # in case an older toolkit alias is registered instead.
                list_result = self._invoke_tool(
                    tools,
                    ('GOOGLEDRIVE_LIST_FILES', 'googledrive_search_files', 'googledrive_list_files', 'googledrive_list_folder'),
                    payload,
                )
            except Exception:
                continue

            file_items = self._coerce_file_items(list_result)
            if file_items:
                break

        if not file_items:
            return []

        results: list[dict[str, Any]] = []
        for item in file_items:
            file_id = item.get('id') or item.get('fileId')
            name = item.get('name') or item.get('title')
            if not self._looks_like_review_file(name) or not file_id:
                continue

            content_payload: Any | None = None
            for tool_name in (
                # GOOGLEDRIVE_DOWNLOAD_FILE is the confirmed real Composio
                # action name; the lowercase ones are kept only as a
                # defensive fallback in case an older toolkit alias is
                # registered instead.
                'GOOGLEDRIVE_DOWNLOAD_FILE',
                'googledrive_get_file_content',
                'googledrive_read_file',
                'googledrive_download_file',
                'googledrive_export_file',
            ):
                try:
                    content_payload = self._invoke_tool(
                        tools,
                        (tool_name,),
                        {'fileId': file_id, 'file_id': file_id, 'id': file_id, 'name': name, 'file_name': name},
                    )
                    break
                except Exception:
                    continue

            if content_payload is None:
                continue

            content = self._resolve_downloaded_content(content_payload)
            if content is None:
                continue

            results.append({'id': file_id, 'name': name, 'content': content})

        return results

    def parse_drive_review_payload(self, raw_payload: Any) -> list[dict[str, Any]]:
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                return []

        if isinstance(raw_payload, dict):
            if isinstance(raw_payload.get('reviews'), list):
                return [item for item in raw_payload['reviews'] if isinstance(item, dict)]
            if isinstance(raw_payload.get('items'), list):
                return [item for item in raw_payload['items'] if isinstance(item, dict)]
            if isinstance(raw_payload.get('files'), list):
                return [item for item in raw_payload['files'] if isinstance(item, dict)]
        if isinstance(raw_payload, list):
            return [item for item in raw_payload if isinstance(item, dict)]
        return []

    def extract_review_files_from_folder(self, folder_id: str | None = None, folder_name: str | None = None) -> list[dict[str, Any]]:
        raw_items = self.read_review_files(folder_id=folder_id, folder_name=folder_name)
        parsed: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            content = item.get('content')
            if not content:
                continue
            if isinstance(content, str):
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    continue
                parsed.extend(self.parse_drive_review_payload(payload))
            elif isinstance(content, (dict, list)):
                parsed.extend(self.parse_drive_review_payload(content))
        return parsed


class DriveIngestion:
    def __init__(
        self,
        folder_id: str | None = None,
        composio_api_key: str | None = None,
        composio_user_id: str | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.folder_id = folder_id or os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        self.folder_name = os.getenv('GOOGLE_DRIVE_FOLDER', 'JVTO Reviews')
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2])
        self.connector = ComposioConnector(
            api_key=composio_api_key or os.getenv('COMPOSIO_API_KEY'),
            user_id=composio_user_id or os.getenv('COMPOSIO_USER_ID'),
            project_root=self.project_root,
        )

    def is_configured(self) -> bool:
        return self.connector.is_configured()

    def export_reviews_to_json(self, output_path: Path) -> list[dict[str, Any]]:
        reviews = self.load_json_reviews()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({'reviews': reviews}, indent=2), encoding='utf-8')
        return reviews

    def load_json_reviews(self) -> list[dict[str, Any]]:
        return self.connector.extract_review_files_from_folder(folder_id=self.folder_id, folder_name=self.folder_name)
