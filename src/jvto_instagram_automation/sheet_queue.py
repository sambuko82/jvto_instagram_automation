from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

EXECUTE_URL = 'https://backend.composio.dev/api/v3/tools/execute/'

COL_NO = 0
COL_BOOKING_ID = 1
COL_CUSTOMER = 2
COL_PACKAGE = 3
COL_CREW = 4
COL_INSTAGRAM_USERNAMES = 5
COL_LISTED_BY = 6
COL_LINKS = 7
COL_CAPTION = 8
COL_IS_UPLOADED = 9
COL_UPLOADED_AT = 10

WIDTH = 11
FIRST_DATA_ROW = 2

# Instagram carousels hold 2-10 items. A row outside that range fails at the
# carousel container step, which is after the queue has already chosen it.
MIN_CAROUSEL_ITEMS = 2
MAX_CAROUSEL_ITEMS = 10


def parse_photo_links(cell: str) -> list[str]:
    """The Link Foto cell is one 'Label: url' per line, in carousel order."""
    urls: list[str] = []

    for line in cell.splitlines():
        line = line.strip()
        if not line:
            continue

        # Split on the FIRST ': ' only, so the colon in https:// survives.
        label, separator, url = line.partition(': ')
        if not separator or not url.strip():
            continue

        urls.append(url.strip())

    return urls


@dataclass(slots=True)
class TripRow:
    row_number: int
    values: list[str] = field(default_factory=list)

    @property
    def booking_id(self) -> str:
        return self.values[COL_BOOKING_ID].strip()

    @property
    def caption(self) -> str:
        return self.values[COL_CAPTION]

    @property
    def instagram_usernames(self) -> list[str]:
        """Crew handles for Instagram collaborator tags, if any were recorded."""
        raw = self.values[COL_INSTAGRAM_USERNAMES]

        return [u.strip().lstrip('@') for u in raw.split(',') if u.strip()]

    @property
    def photo_urls(self) -> list[str]:
        return parse_photo_links(self.values[COL_LINKS])

    @property
    def is_uploaded(self) -> bool:
        return self.values[COL_IS_UPLOADED].strip().upper() == 'TRUE'

    @property
    def uploaded_at(self) -> str:
        return self.values[COL_UPLOADED_AT].strip()


def rows_from_values(values: list[Any]) -> list[TripRow]:
    """Google omits trailing blanks and returns blank rows as empty lists, so
    every row is padded to the full width before any column is indexed."""
    rows: list[TripRow] = []

    for index, value in enumerate(values):
        cells = [str(cell) for cell in (value or [])]
        cells += [''] * (WIDTH - len(cells))

        if not cells[COL_BOOKING_ID].strip():
            continue

        rows.append(TripRow(row_number=FIRST_DATA_ROW + index, values=cells[:WIDTH]))

    return rows


def next_pending(rows: list[TripRow]) -> TripRow | None:
    """The oldest row that is complete, postable, and not yet posted.

    A row Instagram cannot accept is skipped rather than returned. Without
    this, one malformed row stalls the queue forever: publishing fails at the
    carousel container step, run_post_trip returns 1 without marking the row,
    and every following daily run picks the same row and fails the same way,
    so no later trip is ever posted. The sheet is hand-edited by design, so a
    row with a single URL - or one whose URLs were mangled by a destination
    name containing ": " - is an expected accident, not a hypothetical.

    Every skip is printed with its booking id and row number so a human
    reading the Actions log can find the offending row and repair it.
    """
    for row in rows:
        if row.is_uploaded:
            continue

        if not row.caption.strip():
            print(f'Skipping sheet row {row.row_number} ({row.booking_id}): the Caption cell is empty.')
            continue

        count = len(row.photo_urls)
        if not MIN_CAROUSEL_ITEMS <= count <= MAX_CAROUSEL_ITEMS:
            print(
                f'Skipping sheet row {row.row_number} ({row.booking_id}): '
                f'the Link Foto cell parses to {count} photo URL(s), but an Instagram '
                f'carousel needs {MIN_CAROUSEL_ITEMS}-{MAX_CAROUSEL_ITEMS}. '
                'Fix that cell by hand - one "Label: url" per line - and it will be picked up.'
            )
            continue

        return row

    return None


def days_since_last_upload(rows: list[TripRow], now: datetime) -> float | None:
    timestamps = []

    for row in rows:
        if not row.uploaded_at:
            continue
        try:
            parsed = datetime.fromisoformat(row.uploaded_at.replace('Z', '+00:00'))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        timestamps.append(parsed)

    if not timestamps:
        return None

    return (now - max(timestamps)).total_seconds() / 86400


class SheetQueue:
    """Reads the trip photo queue and marks rows posted.

    Only BATCH_GET and BATCH_UPDATE are used - GOOGLESHEETS_VALUES_UPDATE is
    listed by the Composio CLI's local definitions but the backend rejects it
    with Tool_ToolNotFound.
    """

    def __init__(self, api_key: str, user_id: str, spreadsheet_id: str, sheet_name: str = 'Sheet1') -> None:
        self.api_key = api_key
        self.user_id = user_id
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name

    def _execute(self, slug: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            EXECUTE_URL + slug,
            headers={'x-api-key': self.api_key, 'Content-Type': 'application/json'},
            json={'user_id': self.user_id, 'arguments': arguments},
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()

        if body.get('successful') is not True:
            raise RuntimeError(f"Composio {slug} failed: {body.get('error')}")

        return body.get('data') or {}

    def fetch_rows(self) -> list[TripRow]:
        data = self._execute('GOOGLESHEETS_BATCH_GET', {
            'spreadsheet_id': self.spreadsheet_id,
            'ranges': [f'{self.sheet_name}!A{FIRST_DATA_ROW}:K'],
        })
        value_ranges = data.get('valueRanges') or data.get('value_ranges') or []
        values = value_ranges[0].get('values', []) if value_ranges else []

        return rows_from_values(values)

    def next_pending(self) -> TripRow | None:
        return next_pending(self.fetch_rows())

    def mark_uploaded(self, row: TripRow, when: datetime) -> None:
        self._execute('GOOGLESHEETS_BATCH_UPDATE', {
            'spreadsheet_id': self.spreadsheet_id,
            'sheet_name': self.sheet_name,
            'first_cell_location': f'J{row.row_number}',
            'valueInputOption': 'RAW',
            'values': [['TRUE', when.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')]],
        })
