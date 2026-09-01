from datetime import datetime, timedelta, timezone
from pathlib import Path

import jvto_instagram_automation.cli as cli
from jvto_instagram_automation.cli import run_post_trip
from jvto_instagram_automation.config import Settings
from jvto_instagram_automation.sheet_queue import rows_from_values


def make_settings(**overrides) -> Settings:
    defaults = dict(
        project_root=Path('.'),
        output_dir=Path('.'),
        file_id='unused',
        composio_api_key='test-key',
        composio_user_id='jvto_automation',
        instagram_user_id='IGUSER',
        trip_photo_spreadsheet_id='SHEETID',
        trip_photo_sheet_name='Sheet1',
        trip_post_interval_days=4,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _row(no="1", booking="JVTO-1", customer="Cust", package="P", crew="C",
         instagram="", listed_by="Boy", links="", caption="cap",
         uploaded="FALSE", uploaded_at=""):
    """Build a sheet row by name, so a column shift is a one-line repair."""
    return [no, booking, customer, package, crew, instagram, listed_by,
            links, caption, uploaded, uploaded_at]


class FakeQueue:
    """Stands in for SheetQueue. `mark_uploaded_exception` can be a single
    exception (raised every attempt) so the retry-then-report path can be
    exercised without touching the network."""

    def __init__(self, rows, mark_uploaded_exception=None):
        self.rows = rows
        self.fetch_rows_calls = 0
        self.mark_uploaded_calls = []
        self._mark_uploaded_exception = mark_uploaded_exception

    def fetch_rows(self):
        self.fetch_rows_calls += 1
        return self.rows

    def mark_uploaded(self, row, when):
        self.mark_uploaded_calls.append((row, when))
        if self._mark_uploaded_exception is not None:
            raise self._mark_uploaded_exception


class FakePublisher:
    """Stands in for ComposioPublisher.publish_carousel."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def publish_carousel(self, image_urls, caption, instagram_user_id=None, collaborators=None):
        self.calls.append((image_urls, caption, instagram_user_id, collaborators or []))
        return self.result


def test_gate_declines_when_four_days_have_not_elapsed(capsys) -> None:
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace('+00:00', 'Z')
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: u1", caption="cap", uploaded="TRUE", uploaded_at=recent),
        _row(no="2", booking="JVTO-2", customer="B", package="P", crew="C", links="Pickup: u2", caption="cap", uploaded="FALSE", uploaded_at=""),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher)

    assert rc == 0
    assert publisher.calls == []
    assert queue.mark_uploaded_calls == []
    assert 'waiting for 4' in capsys.readouterr().out


def test_no_pending_rows_is_a_clean_no_op() -> None:
    rows = rows_from_values([])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher)

    assert rc == 0
    assert publisher.calls == []
    assert queue.mark_uploaded_calls == []


def test_successful_publish_marks_the_correct_row_exactly_once() -> None:
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE", uploaded_at=""),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher)

    assert rc == 0
    assert len(publisher.calls) == 1
    assert publisher.calls[0][0] == ['u1', 'u2']
    assert len(queue.mark_uploaded_calls) == 1
    marked_row, _when = queue.mark_uploaded_calls[0]
    assert marked_row.booking_id == 'JVTO-1'
    assert marked_row.row_number == 2


def test_publish_failure_does_not_mark_the_row() -> None:
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE", uploaded_at=""),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'error', 'message': 'boom'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher)

    assert rc == 1
    assert len(publisher.calls) == 1
    assert queue.mark_uploaded_calls == []


def test_missing_configuration_exits_without_touching_the_queue() -> None:
    settings = make_settings(composio_api_key=None)
    queue = FakeQueue(rows_from_values([]))
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(settings, force=False, queue=queue, publisher=publisher)

    assert rc == 1
    assert queue.fetch_rows_calls == 0
    assert publisher.calls == []


def test_force_bypasses_the_gate() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace('+00:00', 'Z')
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: u1\nDrop: u1b", caption="cap", uploaded="TRUE", uploaded_at=recent),
        _row(no="2", booking="JVTO-2", customer="B", package="P", crew="C", links="Pickup: u2\nDrop: u2b", caption="cap", uploaded="FALSE", uploaded_at=""),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher)

    assert rc == 0
    assert len(publisher.calls) == 1
    assert len(queue.mark_uploaded_calls) == 1


def test_publish_succeeds_but_marking_fails_every_attempt_is_reported_and_nonzero(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli.time, 'sleep', lambda _seconds: None)

    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE", uploaded_at=""),
    ])
    queue = FakeQueue(rows, mark_uploaded_exception=RuntimeError('BATCH_UPDATE failed: 503'))
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher)

    assert rc != 0
    # The publish itself must not be retried - only the marking.
    assert len(publisher.calls) == 1
    # Bounded retry: three attempts to mark, then give up.
    assert len(queue.mark_uploaded_calls) == 3

    out = capsys.readouterr().out
    assert 'JVTO-1' in out
    assert 'row 2' in out
    assert 'WAS PUBLISHED' in out


def test_the_row_s_instagram_usernames_are_passed_as_collaborators(capsys) -> None:
    rows = rows_from_values([
        _row(booking="JVTO-9", instagram="anjasstywn, kiki.the.explorer",
             links="Pickup: u1\nDrop: u2", uploaded="FALSE"),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher)

    assert rc == 0
    assert publisher.calls[0][3] == ["anjasstywn", "kiki.the.explorer"]
    assert "Collaborators tagged: anjasstywn, kiki.the.explorer" in capsys.readouterr().out


def test_a_post_still_counts_when_instagram_refused_the_collaborators(capsys) -> None:
    # A crew member who renamed their account should cost the trip its credit,
    # not its post.
    rows = rows_from_values([
        _row(booking="JVTO-9", instagram="gone_handle",
             links="Pickup: u1\nDrop: u2", uploaded="FALSE"),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published', 'dropped_collaborators': ['gone_handle']})

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher)

    assert rc == 0
    assert len(queue.mark_uploaded_calls) == 1
    out = capsys.readouterr().out
    assert "Collaborators tagged" not in out
