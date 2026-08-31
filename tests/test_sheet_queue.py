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


def _links(count, prefix="u"):
    return "\n".join(f"Stop {n}: https://i.ibb.co/{prefix}{n}.jpg" for n in range(1, count + 1))


def test_next_pending_skips_uploaded_rows():
    rows = rows_from_values([
        ["1", "JVTO-1", "A", "P", "C", _links(2, "a"), "cap", "TRUE", "2026-08-20T02:00:00Z"],
        ["2", "JVTO-2", "B", "P", "C", _links(2, "b"), "cap", "FALSE", ""],
    ])

    pending = next_pending(rows)

    assert pending.booking_id == "JVTO-2"
    assert pending.photo_urls == ["https://i.ibb.co/b1.jpg", "https://i.ibb.co/b2.jpg"]


def test_next_pending_ignores_rows_with_no_caption_or_no_photos():
    rows = rows_from_values([
        ["1", "JVTO-1", "A", "P", "C", "", "cap", "FALSE", ""],
        ["2", "JVTO-2", "B", "P", "C", _links(2), "", "FALSE", ""],
        ["3", "JVTO-3", "C", "P", "C", _links(2), "cap", "FALSE", ""],
    ])

    assert next_pending(rows).booking_id == "JVTO-3"


def test_next_pending_skips_a_row_with_too_few_photos_for_a_carousel(capsys):
    # One URL fails at the carousel container step, which is after the row has
    # been chosen, so the run dies without marking it and retries it forever.
    rows = rows_from_values([
        ["1", "JVTO-1", "A", "P", "C", "Pickup: https://i.ibb.co/only.jpg", "cap", "FALSE", ""],
        ["2", "JVTO-2", "B", "P", "C", _links(2), "cap", "FALSE", ""],
    ])

    assert next_pending(rows).booking_id == "JVTO-2"

    logged = capsys.readouterr().out
    assert "JVTO-1" in logged
    assert "sheet row 2" in logged
    assert "1 photo URL(s)" in logged


def test_next_pending_skips_a_row_with_too_many_photos_for_a_carousel(capsys):
    rows = rows_from_values([
        ["1", "JVTO-1", "A", "P", "C", _links(11), "cap", "FALSE", ""],
        ["2", "JVTO-2", "B", "P", "C", _links(3), "cap", "FALSE", ""],
    ])

    assert next_pending(rows).booking_id == "JVTO-2"

    logged = capsys.readouterr().out
    assert "JVTO-1" in logged
    assert "sheet row 2" in logged
    assert "11 photo URL(s)" in logged


def test_next_pending_accepts_the_carousel_bounds_exactly():
    assert next_pending(rows_from_values([
        ["1", "JVTO-1", "A", "P", "C", _links(2), "cap", "FALSE", ""],
    ])).booking_id == "JVTO-1"

    assert next_pending(rows_from_values([
        ["1", "JVTO-1", "A", "P", "C", _links(10), "cap", "FALSE", ""],
    ])).booking_id == "JVTO-1"


def test_next_pending_names_a_row_it_skips_for_an_empty_caption(capsys):
    rows = rows_from_values([["1", "JVTO-1", "A", "P", "C", _links(2), "  ", "FALSE", ""]])

    assert next_pending(rows) is None
    assert "JVTO-1" in capsys.readouterr().out


def test_next_pending_is_none_when_everything_is_posted():
    rows = rows_from_values([["1", "JVTO-1", "A", "P", "C", _links(2), "cap", "TRUE", "2026-08-20T02:00:00Z"]])

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
