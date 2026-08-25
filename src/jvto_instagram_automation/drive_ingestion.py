from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass(slots=True)
class DriveAuthState:
    folder_id: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None


class DriveIngestion:
    def __init__(self, folder_id: str | None = None, access_token: str | None = None) -> None:
        self.folder_id = folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        self.access_token = access_token or os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN")

    def list_review_files(self) -> list[dict[str, Any]]:
        if not self.folder_id:
            return []
        if not self.access_token:
            return []

        url = "https://www.googleapis.com/drive/v3/files"
        params = {
            "q": f"'{self.folder_id}' in parents and trashed = false",
            "fields": "files(id,name,mimeType,webViewLink)",
            "pageSize": 100,
        }
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(url, params=params, headers=headers, timeout=60)
        response.raise_for_status()
        payload = response.json()
        return payload.get("files", [])

    def download_file_content(self, file_id: str) -> str | None:
        if not self.access_token:
            return None
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        return response.text

    def load_json_reviews(self) -> list[dict[str, Any]]:
        files = self.list_review_files()
        reviews: list[dict[str, Any]] = []
        for item in files:
            name = item.get("name", "")
            if not name.lower().endswith(".json"):
                continue
            content = self.download_file_content(item["id"])
            if not content:
                continue
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and "reviews" in payload:
                reviews.extend(payload.get("reviews", []))
            elif isinstance(payload, list):
                reviews.extend(payload)
        return reviews

    def export_reviews_to_json(self, output_path: Path) -> list[dict[str, Any]]:
        reviews = self.load_json_reviews()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"reviews": reviews}, indent=2), encoding="utf-8")
        return reviews
