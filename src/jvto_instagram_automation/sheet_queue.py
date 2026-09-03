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
# Matches the Meta catalog's retailer_id verbatim, e.g. package-SUB-3D2N-003.
COL_PACKAGE_CODE = 4
COL_CREW = 5
COL_INSTAGRAM_USERNAMES = 6
COL_LISTED_BY = 7
COL_LINKS = 8
COL_CAPTION = 9
# One pair per platform: a trip can be live on Instagram and still queued for
# Facebook, and the publisher completes whichever half is missing.
COL_IS_UPLOADED_IG = 10
COL_UPLOADED_AT_IG = 11
COL_IS_UPLOADED_FB = 12
COL_UPLOADED_AT_FB = 13
# Posting order, lowest first. Written by the studio panel; blank on rows the
# crew just submitted, which sorts them after every numbered row.
COL_PRIORITY = 14

WIDTH = 15

# Column letters for the two 'Is Uploaded' cells, which mark_uploaded writes.
UPLOADED_CELL = {'instagram': 'K', 'facebook': 'M'}
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
    def package_code(self) -> str:
        """Meta catalog retailer_id for this trip's package, or '' if there is none.

        The crew portal writes '-' when it cannot resolve one - a KLOOK package
        with no website twin, or a booking with no package at all - and that
        sentinel is normalised away here so callers only ever see a real code or
        nothing.
        """
        code = self.values[COL_PACKAGE_CODE].strip()

        return '' if code == '-' else code

    @property
    def instagram_usernames(self) -> list[str]:
        """Crew handles for Instagram collaborator tags, if any were recorded."""
        raw = self.values[COL_INSTAGRAM_USERNAMES]

        return [u.strip().lstrip('@') for u in raw.split(',') if u.strip()]

    @property
    def photo_urls(self) -> list[str]:
        return parse_photo_links(self.values[COL_LINKS])

    @property
    def is_uploaded_ig(self) -> bool:
        return self.values[COL_IS_UPLOADED_IG].strip().upper() == 'TRUE'

    @property
    def is_uploaded_fb(self) -> bool:
        return self.values[COL_IS_UPLOADED_FB].strip().upper() == 'TRUE'

    @property
    def is_uploaded(self) -> bool:
        """Fully done - live on both platforms, nothing left to publish."""
        return self.is_uploaded_ig and self.is_uploaded_fb

    @property
    def is_partly_uploaded(self) -> bool:
        """Live on one platform but not the other, so it owes a post."""
        return self.is_uploaded_ig != self.is_uploaded_fb

    @property
    def priority(self) -> float:
        """Where the operator put this trip in the queue.

        Blank means nobody chose a position, so it sorts after every trip that
        has one - a newly submitted trip joins the back rather than jumping
        ahead of an order someone arranged by hand.
        """
        raw = self.values[COL_PRIORITY].strip()
        try:
            return float(raw)
        except ValueError:
            return float('inf')

    @property
    def uploaded_at(self) -> str:
        """The Instagram timestamp, which is what paces the schedule.

        Facebook is a companion post rather than a cadence of its own, so the
        interval gate reads this one.
        """
        return self.values[COL_UPLOADED_AT_IG].strip()


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


def next_unfinished(rows: list[TripRow]) -> TripRow | None:
    """The oldest row that owes a post on a platform it has already reached.

    Instagram and Facebook are published in the same run, but either can fail
    on its own. When that happens the trip is half public, and finishing it is
    not a new post on the schedule - it is repairing one already made. So this
    is looked for before the interval gate is consulted, and a trip stranded on
    one platform is completed the next day rather than four days later.
    """
    for row in rows:
        if row.is_partly_uploaded and _is_postable(row, quiet=True):
            return row

    return None


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
    # Sorted by the operator's order, then by sheet position for the rows that
    # have none. Stable on row_number so two blanks keep submission order.
    for row in sorted(rows, key=lambda r: (r.priority, r.row_number)):
        if row.is_uploaded:
            continue

        if _is_postable(row):
            return row

    return None


def _is_postable(row: TripRow, quiet: bool = False) -> bool:
    """Whether the row holds something publishable, reporting why if not.

    Quiet for the half-finished scan, which runs first over the same rows: a
    row that is broken gets its reason printed once by next_pending rather than
    twice by two passes.
    """
    if not row.caption.strip():
        if not quiet:
            print(f'Skipping sheet row {row.row_number} ({row.booking_id}): the Caption cell is empty.')
        return False

    count = len(row.photo_urls)
    if not MIN_CAROUSEL_ITEMS <= count <= MAX_CAROUSEL_ITEMS:
        if not quiet:
            print(
                f'Skipping sheet row {row.row_number} ({row.booking_id}): '
                f'the Link Foto cell parses to {count} photo URL(s), but an Instagram '
                f'carousel needs {MIN_CAROUSEL_ITEMS}-{MAX_CAROUSEL_ITEMS}. '
                'Fix that cell by hand - one "Label: url" per line - and it will be picked up.'
            )
        return False

    return True


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
            'ranges': [f'{self.sheet_name}!A{FIRST_DATA_ROW}:O'],
        })
        value_ranges = data.get('valueRanges') or data.get('value_ranges') or []
        values = value_ranges[0].get('values', []) if value_ranges else []

        return rows_from_values(values)

    def next_pending(self) -> TripRow | None:
        return next_pending(self.fetch_rows())

    def mark_uploaded(self, row: TripRow, when: datetime, platform: str = 'instagram') -> None:
        cell = UPLOADED_CELL[platform]

        self._execute('GOOGLESHEETS_BATCH_UPDATE', {
            'spreadsheet_id': self.spreadsheet_id,
            'sheet_name': self.sheet_name,
            'first_cell_location': f'{cell}{row.row_number}',
            'valueInputOption': 'RAW',
            'values': [['TRUE', when.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')]],
        })
