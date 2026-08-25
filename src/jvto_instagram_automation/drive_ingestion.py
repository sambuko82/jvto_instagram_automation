from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DriveAuthState:
    session_id: str | None = None
    connection_request_id: str | None = None
    redirect_url: str | None = None
    status: str = "pending"
    folder_name: str | None = None


class ComposioConnector:
    def __init__(
        self,
        api_key: str | None = None,
        user_id: str | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("COMPOSIO_API_KEY")
        self.user_id = user_id or os.getenv("COMPOSIO_USER_ID")
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2])
        self.state_path = self.project_root / ".drive_auth.json"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def load_state(self) -> DriveAuthState | None:
        if not self.state_path.exists():
            return None
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return DriveAuthState(**payload)

    def save_state(self, state: DriveAuthState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")

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
        return composio.use(auth_state.session_id)

    def _invoke_tool(self, tools: Any, names: tuple[str, ...], kwargs: dict[str, Any]) -> Any:
        if tools is None:
            raise RuntimeError("Composio tools are not available")

        for tool_name in names:
            for method_name in ("execute", "run", "invoke"):
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
        raise RuntimeError("No compatible Google Drive tool call succeeded")

    def _coerce_file_items(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("files", "items", "results", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            if isinstance(payload.get("file"), dict):
                return [payload["file"]]
        return []

    def _extract_content(self, payload: Any) -> str | None:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="ignore")
        if isinstance(payload, dict):
            for key in ("content", "data", "text", "body"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
            if isinstance(payload.get("file"), dict):
                return self._extract_content(payload["file"])
        return None

    def _looks_like_review_file(self, name: str | None) -> bool:
        if not name:
            return False
        lowered = name.lower()
        if lowered.endswith(".json"):
            return True
        if "review" in lowered and (lowered.endswith(".csv") or lowered.endswith(".txt")):
            return True
        return False

    def start_auth_flow(self, folder_name: str | None = None) -> dict[str, Any]:
        if not self.api_key:
            return {"status": "missing_api_key", "message": "Set COMPOSIO_API_KEY to start a Google Drive auth flow."}

        try:
            from composio import Composio
        except Exception as exc:  # pragma: no cover - optional dependency path
            return {
                "status": "unavailable",
                "message": f"Composio SDK is not available in this environment: {exc}",
            }

        composio = Composio(api_key=self.api_key)
        session = composio.create(user_id=self.user_id or "jvto", toolkits=["googledrive"])
        connection_request = session.authorize("googledrive")

        state = DriveAuthState(
            session_id=getattr(session, "session_id", None),
            connection_request_id=getattr(connection_request, "id", None),
            redirect_url=getattr(connection_request, "redirect_url", None),
            status=getattr(connection_request, "status", "pending"),
            folder_name=folder_name,
        )
        self.save_state(state)

        return {
            "status": "auth_started",
            "message": "Open the redirect URL to authorize the Google Drive connection.",
            "redirect_url": state.redirect_url,
            "session_id": state.session_id,
            "connection_request_id": state.connection_request_id,
        }

    def read_review_files(self, folder_name: str | None = None) -> list[dict[str, Any]]:
        session = self._get_session()
        if session is None:
            return []

        try:
            tools = session.tools()
        except Exception:
            tools = None

        if tools is None:
            return []

        state = self.load_state()
        folder = folder_name or (state.folder_name if state else None) or os.getenv("GOOGLE_DRIVE_FOLDER", "JVTO Reviews")

        search_payloads = (
            {"query": folder},
            {"folder_name": folder},
            {"name": folder},
        )

        file_items: list[dict[str, Any]] = []
        for payload in search_payloads:
            try:
                search_result = self._invoke_tool(
                    tools,
                    ("googledrive_search_files", "googledrive_list_files", "googledrive_list_folder"),
                    payload,
                )
            except Exception:
                continue

            file_items = self._coerce_file_items(search_result)
            if file_items:
                break

        if not file_items:
            return []

        results: list[dict[str, Any]] = []
        for item in file_items:
            file_id = item.get("id") or item.get("fileId")
            name = item.get("name") or item.get("title")
            if not self._looks_like_review_file(name):
                continue
            if not file_id:
                continue

            content_payload: Any | None = None
            for tool_name in (
                "googledrive_get_file_content",
                "googledrive_read_file",
                "googledrive_download_file",
                "googledrive_export_file",
            ):
                try:
                    content_payload = self._invoke_tool(
                        tools,
                        (tool_name,),
                        {"file_id": file_id, "id": file_id, "name": name, "file_name": name},
                    )
                    break
                except Exception:
                    continue

            if content_payload is None:
                continue

            content = self._extract_content(content_payload)
            if content is None:
                continue

            results.append({"id": file_id, "name": name, "content": content})

        return results

    def parse_drive_review_payload(self, raw_payload: Any) -> list[dict[str, Any]]:
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                return []

        if isinstance(raw_payload, dict):
            if isinstance(raw_payload.get("reviews"), list):
                return [item for item in raw_payload["reviews"] if isinstance(item, dict)]
            if isinstance(raw_payload.get("items"), list):
                return [item for item in raw_payload["items"] if isinstance(item, dict)]
            if isinstance(raw_payload.get("files"), list):
                return [item for item in raw_payload["files"] if isinstance(item, dict)]
        if isinstance(raw_payload, list):
            return [item for item in raw_payload if isinstance(item, dict)]
        return []

    def extract_review_files_from_folder(self, folder_name: str | None = None) -> list[dict[str, Any]]:
        raw_items = self.read_review_files(folder_name)
        parsed: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
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
        access_token: str | None = None,
        composio_api_key: str | None = None,
        composio_user_id: str | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.folder_id = folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID") or os.getenv("GOOGLE_DRIVE_FOLDER")
        self.access_token = access_token or os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN")
        self.composio_api_key = composio_api_key or os.getenv("COMPOSIO_API_KEY")
        self.composio_user_id = composio_user_id or os.getenv("COMPOSIO_USER_ID")
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2])
        self.connector = ComposioConnector(
            api_key=self.composio_api_key,
            user_id=self.composio_user_id,
            project_root=self.project_root,
        )

    def authorize(self) -> dict[str, Any]:
        return self.connector.start_auth_flow(folder_name=self._folder_name())

    def _folder_name(self) -> str:
        return self.folder_id or os.getenv("GOOGLE_DRIVE_FOLDER", "JVTO Reviews")

    def export_reviews_to_json(self, output_path: Path) -> list[dict[str, Any]]:
        reviews = self.load_json_reviews()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"reviews": reviews}, indent=2), encoding="utf-8")
        return reviews

    def load_json_reviews(self) -> list[dict[str, Any]]:
        return self.connector.extract_review_files_from_folder(self._folder_name())
