from datetime import datetime, timezone

from jvto_instagram_automation.sheet_queue import (
    WIDTH,
    TripRow,
    days_since_last_upload,
    next_pending,
    parse_photo_links,
    rows_from_values,
)


def _row(no="1", booking="JVTO-1", customer="Cust", package="P", package_code="",
         crew="C", instagram="", listed_by="Boy", links="", caption="cap",
         uploaded="FALSE", uploaded_at="", uploaded_fb=None, uploaded_at_fb="",
         priority=""):
    """Build a sheet row by name rather than by position.

    The columns have shifted once already; positional literals in every
    fixture is what made that a thirteen-test repair instead of a one-line one.
    """
    # Facebook mirrors Instagram unless a test says otherwise, so existing
    # fixtures keep meaning "posted" or "not posted" rather than accidentally
    # becoming half-finished rows.
    if uploaded_fb is None:
        uploaded_fb = uploaded

    return [no, booking, customer, package, package_code, crew, instagram,
            listed_by, links, caption, uploaded, uploaded_at,
            uploaded_fb, uploaded_at_fb, priority]


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


def test_the_operator_s_order_beats_sheet_order():
    """The panel writes a position per trip; the publisher must honour it
    rather than the order the rows happen to sit in."""
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", links="a: u1\nb: u2", caption="cap", priority="3"),
        _row(no="2", booking="JVTO-2", links="a: u3\nb: u4", caption="cap", priority="1"),
        _row(no="3", booking="JVTO-3", links="a: u5\nb: u6", caption="cap", priority="2"),
    ])

    assert next_pending(rows).booking_id == "JVTO-2"


def test_a_trip_with_no_position_joins_the_back():
    """A trip the crew just submitted has no position anyone chose, so it must
    not jump ahead of an order arranged by hand."""
    rows = rows_from_values([
        _row(no="1", booking="FRESH", links="a: u1\nb: u2", caption="cap", priority=""),
        _row(no="2", booking="ORDERED", links="a: u3\nb: u4", caption="cap", priority="9"),
    ])

    assert next_pending(rows).booking_id == "ORDERED"


def test_two_unordered_trips_keep_submission_order():
    rows = rows_from_values([
        _row(no="1", booking="OLDER", links="a: u1\nb: u2", caption="cap"),
        _row(no="2", booking="NEWER", links="a: u3\nb: u4", caption="cap"),
    ])

    assert next_pending(rows).booking_id == "OLDER"


def test_a_junk_position_does_not_crash_the_queue():
    """The sheet is hand-editable, so a typed position can be anything."""
    rows = rows_from_values([
        _row(no="1", booking="JUNK", links="a: u1\nb: u2", caption="cap", priority="satu"),
        _row(no="2", booking="REAL", links="a: u3\nb: u4", caption="cap", priority="5"),
    ])

    assert next_pending(rows).booking_id == "REAL"


def test_the_order_does_not_override_the_checks():
    """Being put first does not make a row with one photo postable."""
    rows = rows_from_values([
        _row(no="1", booking="BROKEN", links="a: only-one", caption="cap", priority="1"),
        _row(no="2", booking="FINE", links="a: u1\nb: u2", caption="cap", priority="2"),
    ])

    assert next_pending(rows).booking_id == "FINE"
