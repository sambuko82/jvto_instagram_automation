from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    project_root: Path
    output_dir: Path
    file_id: str
    local_json: Path | None = None
    drive_folder_id: str | None = None
    drive_access_token: str | None = None
    composio_api_key: str | None = None
    composio_user_id: str | None = None
    imgbb_api_key: str | None = None
    instagram_access_token: str | None = None
    instagram_user_id: str | None = None
    publish: bool = False
    agentic: bool = False
    agentic_provider: str | None = None


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.strip().startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    _load_dotenv(project_root / '.env')

    output_dir = Path(os.getenv('OUTPUT_DIR', 'output')).resolve()
    if not output_dir.is_absolute():
        output_dir = (project_root / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        project_root=project_root,
        output_dir=output_dir,
        file_id=os.getenv('FILE_ID_GOOGLE_REVIEW_PAGE_1', '1ZlhSg1S1kEyfjA8AZIYL_fLOEFHJHVhn'),
        local_json=Path(os.getenv('LOCAL_JSON', '')).resolve() if os.getenv('LOCAL_JSON') else None,
        drive_folder_id=os.getenv('GOOGLE_DRIVE_FOLDER_ID') or os.getenv('GOOGLE_DRIVE_FOLDER') or None,
        drive_access_token=os.getenv('GOOGLE_DRIVE_ACCESS_TOKEN') or None,
        composio_api_key=os.getenv('COMPOSIO_API_KEY') or None,
        composio_user_id=os.getenv('COMPOSIO_USER_ID') or None,
        imgbb_api_key=os.getenv('IMGBB_API_KEY') or None,
        instagram_access_token=os.getenv('INSTAGRAM_ACCESS_TOKEN') or None,
        instagram_user_id=os.getenv('INSTAGRAM_USER_ID') or None,
        publish=os.getenv('PUBLISH', '0').lower() in {'1', 'true', 'yes'},
        agentic=os.getenv('AGENTIC_EXTRACTION', '0').lower() in {'1', 'true', 'yes'},
        agentic_provider=os.getenv('AGENTIC_PROVIDER') or None,
    )
