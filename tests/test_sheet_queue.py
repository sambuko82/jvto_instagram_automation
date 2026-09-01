from datetime import datetime, timezone

from jvto_instagram_automation.sheet_queue import (
    WIDTH,
    TripRow,
    days_since_last_upload,
    next_pending,
    parse_photo_links,
    rows_from_values,
)


def _row(no="1", booking="JVTO-1", customer="Cust", package="P", crew="C",
         instagram="", listed_by="Boy", links="", caption="cap",
         uploaded="FALSE", uploaded_at=""):
    """Build a sheet row by name rather than by position.

    The columns have shifted once already; positional literals in every
    fixture is what made that a thirteen-test repair instead of a one-line one.
    """
    return [no, booking, customer, package, crew, instagram, listed_by,
            links, caption, uploaded, uploaded_at]


def test_parse_photo_links_keeps_order_and_survives_the_https_colon():
    cell = "Pickup: https://i.ibb.co/a.jpg\nMount Ijen: https://i.ibb.co/b.jpg"

    assert parse_photo_links(cell) == ["https://i.ibb.co/a.jpg", "https://i.ibb.co/b.jpg"]


def test_parse_photo_links_ignores_blank_and_malformed_lines():
    cell = "Pickup: https://i.ibb.co/a.jpg\n\nrubbish\n   \nDrop: https://i.ibb.co/c.jpg"

    assert parse_photo_links(cell) == ["https://i.ibb.co/a.jpg", "https://i.ibb.co/c.jpg"]


def test_rows_are_padded_and_numbered_from_two():
    rows = rows_from_values([["1", "JVTO-1"], [], ["3", "JVTO-3", "Cust"]])

    assert [row.row_number for row in rows] == [2, 4]
    assert len(rows[0].values) == WIDTH
    assert rows[0].values[-1] == ""


def _links(count, prefix="u"):
    return "\n".join(f"Stop {n}: https://i.ibb.co/{prefix}{n}.jpg" for n in range(1, count + 1))


def test_next_pending_skips_uploaded_rows():
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links=_links(2, "a"), caption="cap", uploaded="TRUE", uploaded_at="2026-08-20T02:00:00Z"),
        _row(no="2", booking="JVTO-2", customer="B", package="P", crew="C", links=_links(2, "b"), caption="cap", uploaded="FALSE", uploaded_at=""),
    ])

    pending = next_pending(rows)

    assert pending.booking_id == "JVTO-2"
    assert pending.photo_urls == ["https://i.ibb.co/b1.jpg", "https://i.ibb.co/b2.jpg"]


def test_next_pending_ignores_rows_with_no_caption_or_no_photos():
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="", caption="cap", uploaded="FALSE", uploaded_at=""),
        _row(no="2", booking="JVTO-2", customer="B", package="P", crew="C", links=_links(2), caption="", uploaded="FALSE", uploaded_at=""),
        _row(no="3", booking="JVTO-3", customer="C", package="P", crew="C", links=_links(2), caption="cap", uploaded="FALSE", uploaded_at=""),
    ])

    assert next_pending(rows).booking_id == "JVTO-3"


def test_next_pending_skips_a_row_with_too_few_photos_for_a_carousel(capsys):
    # One URL fails at the carousel container step, which is after the row has
    # been chosen, so the run dies without marking it and retries it forever.
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: https://i.ibb.co/only.jpg", caption="cap", uploaded="FALSE", uploaded_at=""),
        _row(no="2", booking="JVTO-2", customer="B", package="P", crew="C", links=_links(2), caption="cap", uploaded="FALSE", uploaded_at=""),
    ])

    assert next_pending(rows).booking_id == "JVTO-2"

    logged = capsys.readouterr().out
    assert "JVTO-1" in logged
    assert "sheet row 2" in logged
    assert "1 photo URL(s)" in logged


def test_next_pending_skips_a_row_with_too_many_photos_for_a_carousel(capsys):
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links=_links(11), caption="cap", uploaded="FALSE", uploaded_at=""),
        _row(no="2", booking="JVTO-2", customer="B", package="P", crew="C", links=_links(3), caption="cap", uploaded="FALSE", uploaded_at=""),
    ])

    assert next_pending(rows).booking_id == "JVTO-2"

    logged = capsys.readouterr().out
    assert "JVTO-1" in logged
    assert "sheet row 2" in logged
    assert "11 photo URL(s)" in logged


def test_next_pending_accepts_the_carousel_bounds_exactly():
    assert next_pending(rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links=_links(2), caption="cap", uploaded="FALSE", uploaded_at=""),
    ])).booking_id == "JVTO-1"

    assert next_pending(rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links=_links(10), caption="cap", uploaded="FALSE", uploaded_at=""),
    ])).booking_id == "JVTO-1"


def test_next_pending_names_a_row_it_skips_for_an_empty_caption(capsys):
    rows = rows_from_values([["1", "JVTO-1", "A", "P", "C", _links(2), "  ", "FALSE", ""]])

    assert next_pending(rows) is None
    assert "JVTO-1" in capsys.readouterr().out


def test_next_pending_is_none_when_everything_is_posted():
    rows = rows_from_values([["1", "JVTO-1", "A", "P", "C", _links(2), "cap", "TRUE", "2026-08-20T02:00:00Z"]])

    assert next_pending(rows) is None


def test_is_uploaded_accepts_lowercase_and_padded_values():
    row = TripRow(row_number=2, values=_row(uploaded=" true "))

    assert row.is_uploaded is True


def test_days_since_last_upload_uses_the_most_recent_timestamp():
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="", package="", crew="", links="", caption="", uploaded="TRUE", uploaded_at="2026-08-20T02:00:00Z"),
        _row(no="2", booking="JVTO-2", customer="", package="", crew="", links="", caption="", uploaded="TRUE", uploaded_at="2026-08-27T02:00:00Z"),
    ])

    days = days_since_last_upload(rows, datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc))

    assert days == 4.0


def test_days_since_last_upload_is_none_when_nothing_was_ever_posted():
    rows = rows_from_values([["1", "JVTO-1", "", "", "", "", "", "FALSE", ""]])

    assert days_since_last_upload(rows, datetime(2026, 8, 31, tzinfo=timezone.utc)) is None
