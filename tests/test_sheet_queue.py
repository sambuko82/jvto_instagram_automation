from datetime import datetime, timezone

from jvto_instagram_automation.sheet_queue import (
    TripRow,
    days_since_last_upload,
    next_pending,
    parse_photo_links,
    rows_from_values,
)


def test_parse_photo_links_keeps_order_and_survives_the_https_colon():
    cell = "Pickup: https://i.ibb.co/a.jpg\nMount Ijen: https://i.ibb.co/b.jpg"

    assert parse_photo_links(cell) == ["https://i.ibb.co/a.jpg", "https://i.ibb.co/b.jpg"]


def test_parse_photo_links_ignores_blank_and_malformed_lines():
    cell = "Pickup: https://i.ibb.co/a.jpg\n\nrubbish\n   \nDrop: https://i.ibb.co/c.jpg"

    assert parse_photo_links(cell) == ["https://i.ibb.co/a.jpg", "https://i.ibb.co/c.jpg"]


def test_rows_are_padded_and_numbered_from_two():
    rows = rows_from_values([["1", "JVTO-1"], [], ["3", "JVTO-3", "Cust"]])

    assert [row.row_number for row in rows] == [2, 4]
    assert len(rows[0].values) == 9
    assert rows[0].values[8] == ""


def test_next_pending_skips_uploaded_rows():
    rows = rows_from_values([
        ["1", "JVTO-1", "A", "P", "C", "Pickup: u1", "cap", "TRUE", "2026-08-20T02:00:00Z"],
        ["2", "JVTO-2", "B", "P", "C", "Pickup: u2", "cap", "FALSE", ""],
    ])

    pending = next_pending(rows)

    assert pending.booking_id == "JVTO-2"
    assert pending.photo_urls == ["u2"]


def test_next_pending_ignores_rows_with_no_caption_or_no_photos():
    rows = rows_from_values([
        ["1", "JVTO-1", "A", "P", "C", "", "cap", "FALSE", ""],
        ["2", "JVTO-2", "B", "P", "C", "Pickup: u2", "", "FALSE", ""],
        ["3", "JVTO-3", "C", "P", "C", "Pickup: u3", "cap", "FALSE", ""],
    ])

    assert next_pending(rows).booking_id == "JVTO-3"


def test_next_pending_is_none_when_everything_is_posted():
    rows = rows_from_values([["1", "JVTO-1", "A", "P", "C", "Pickup: u1", "cap", "TRUE", "2026-08-20T02:00:00Z"]])

    assert next_pending(rows) is None


def test_is_uploaded_accepts_lowercase_and_padded_values():
    row = TripRow(row_number=2, values=["1", "JVTO-1", "", "", "", "", "", " true ", ""])

    assert row.is_uploaded is True


def test_days_since_last_upload_uses_the_most_recent_timestamp():
    rows = rows_from_values([
        ["1", "JVTO-1", "", "", "", "", "", "TRUE", "2026-08-20T02:00:00Z"],
        ["2", "JVTO-2", "", "", "", "", "", "TRUE", "2026-08-27T02:00:00Z"],
    ])

    days = days_since_last_upload(rows, datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc))

    assert days == 4.0


def test_days_since_last_upload_is_none_when_nothing_was_ever_posted():
    rows = rows_from_values([["1", "JVTO-1", "", "", "", "", "", "FALSE", ""]])

    assert days_since_last_upload(rows, datetime(2026, 8, 31, tzinfo=timezone.utc)) is None
