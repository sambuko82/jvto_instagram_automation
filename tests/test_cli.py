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


def _row(no="1", booking="JVTO-1", customer="Cust", package="P", package_code="",
         crew="C", instagram="", listed_by="Boy", links="", caption="cap",
         uploaded="FALSE", uploaded_at="", uploaded_fb=None, uploaded_at_fb=""):
    """Build a sheet row by name, so a column shift is a one-line repair."""
    # Facebook mirrors Instagram unless a test says otherwise, so existing
    # fixtures keep meaning "posted" or "not posted" rather than accidentally
    # becoming half-finished rows.
    if uploaded_fb is None:
        uploaded_fb = uploaded

    return [no, booking, customer, package, package_code, crew, instagram,
            listed_by, links, caption, uploaded, uploaded_at,
            uploaded_fb, uploaded_at_fb]


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

    def mark_uploaded(self, row, when, platform='instagram'):
        self.mark_uploaded_calls.append((row, when, platform))
        if self._mark_uploaded_exception is not None:
            raise self._mark_uploaded_exception


def _marked(queue, platform):
    """How many times the row was marked uploaded for one platform."""
    return [c for c in queue.mark_uploaded_calls if c[2] == platform]


class FakeFacebookPublisher:
    """Stands in for FacebookPublisher.publish_photo_post.

    Every run_post_trip call passes one. Without it the CLI reaches the real
    publisher and the suite talks to the network - which is exactly how this
    class came to exist.
    """

    def __init__(self, result=None):
        self.result = result or {'status': 'published', 'post_id': 'fb_1'}
        self.calls = []

    def publish_photo_post(self, image_urls, message, page_name=None):
        self.calls.append((image_urls, message, page_name))
        return self.result


class FakePublisher:
    """Stands in for ComposioPublisher.publish_carousel."""

    def __init__(self, result):
        self.result = result
        self.calls = []
        self.package_codes = []

    def publish_carousel(self, image_urls, caption, instagram_user_id=None, collaborators=None,
                         package_code=None):
        self.calls.append((image_urls, caption, instagram_user_id, collaborators or []))
        self.package_codes.append(package_code)
        return self.result


def test_gate_declines_when_four_days_have_not_elapsed(capsys) -> None:
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace('+00:00', 'Z')
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: u1", caption="cap", uploaded="TRUE", uploaded_at=recent),
        _row(no="2", booking="JVTO-2", customer="B", package="P", crew="C", links="Pickup: u2", caption="cap", uploaded="FALSE", uploaded_at=""),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert rc == 0
    assert publisher.calls == []
    assert queue.mark_uploaded_calls == []
    assert 'waiting for 4' in capsys.readouterr().out


def test_no_pending_rows_is_a_clean_no_op() -> None:
    rows = rows_from_values([])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert rc == 0
    assert publisher.calls == []
    assert queue.mark_uploaded_calls == []


def test_successful_publish_marks_the_correct_row_exactly_once() -> None:
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE", uploaded_at=""),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert rc == 0
    assert len(publisher.calls) == 1
    assert publisher.calls[0][0] == ['u1', 'u2']
    # One run now publishes both platforms, and each marks its own cell.
    assert len(_marked(queue, 'instagram')) == 1
    assert len(_marked(queue, 'facebook')) == 1
    marked_row, _when, _platform = queue.mark_uploaded_calls[0]
    assert marked_row.booking_id == 'JVTO-1'
    assert marked_row.row_number == 2


def test_publish_failure_does_not_mark_the_row() -> None:
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE", uploaded_at=""),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'error', 'message': 'boom'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert rc == 1
    assert len(publisher.calls) == 1
    # Instagram failed, so its cell stays FALSE and the run reports non-zero.
    # Facebook is independent and still went out - the whole reason the two
    # platforms have separate columns. The next run finds the row half done
    # and retries only Instagram.
    assert _marked(queue, 'instagram') == []
    assert len(_marked(queue, 'facebook')) == 1


def test_missing_configuration_exits_without_touching_the_queue() -> None:
    settings = make_settings(composio_api_key=None)
    queue = FakeQueue(rows_from_values([]))
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(settings, force=False, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

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

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert rc == 0
    assert len(publisher.calls) == 1
    assert len(_marked(queue, 'instagram')) == 1


def test_publish_succeeds_but_marking_fails_every_attempt_is_reported_and_nonzero(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli.time, 'sleep', lambda _seconds: None)

    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", customer="A", package="P", crew="C", links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE", uploaded_at=""),
    ])
    queue = FakeQueue(rows, mark_uploaded_exception=RuntimeError('BATCH_UPDATE failed: 503'))
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert rc != 0
    # The publish itself must not be retried - only the marking.
    assert len(publisher.calls) == 1
    # Bounded retry: three attempts to mark, then give up.
    assert len(_marked(queue, 'instagram')) == 3

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

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

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

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert rc == 0
    assert len(_marked(queue, 'instagram')) == 1
    out = capsys.readouterr().out
    assert "Collaborators tagged" not in out


def test_the_row_s_package_code_is_passed_to_the_publisher() -> None:
    rows = rows_from_values([
        _row(no="1", booking="JVTO-9", package_code="package-SUB-3D2N-003",
             links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE"),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published', 'product_tagged': True})

    run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert publisher.package_codes == ['package-SUB-3D2N-003']


def test_a_post_still_counts_when_the_product_tag_was_refused(capsys) -> None:
    """The requirement in one test: Instagram refused the product tag, the
    carousel went out anyway, and the row is marked uploaded so the queue moves
    on. A row left pending here would be re-posted on the next run."""
    rows = rows_from_values([
        _row(no="1", booking="JVTO-9", package_code="package-SUB-3D2N-003",
             links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE"),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({
        'status': 'published',
        'product_tagged': False,
        'product_tag_skipped': 'package-SUB-3D2N-003 is not in the shop catalog',
    })

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert rc == 0
    assert len(_marked(queue, 'instagram')) == 1

    out = capsys.readouterr().out
    assert 'Instagram: published JVTO-9' in out
    assert 'Product NOT tagged' in out
    assert 'not in the shop catalog' in out


def test_a_row_with_no_package_code_says_nothing_about_products(capsys) -> None:
    rows = rows_from_values([
        _row(no="1", booking="JVTO-9", package_code="-",
             links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE"),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher, fb_publisher=FakeFacebookPublisher())

    assert rc == 0
    assert publisher.package_codes == ['']
    assert 'Product' not in capsys.readouterr().out


def test_a_half_finished_trip_is_completed_before_the_gate_is_consulted(capsys) -> None:
    """Instagram went out; Facebook did not. Finishing that is a repair, not a
    new post, so it must not wait four days for the interval gate to reopen
    while half the trip sits public and unmatched."""
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace('+00:00', 'Z')
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", links="Pickup: u1\nDrop: u2", caption="cap",
             uploaded="TRUE", uploaded_at=recent, uploaded_fb="FALSE"),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})
    fb = FakeFacebookPublisher()

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher, fb_publisher=fb)

    assert rc == 0
    assert publisher.calls == []                    # Instagram already done
    assert len(fb.calls) == 1                       # Facebook caught up
    assert len(_marked(queue, 'facebook')) == 1
    assert 'waiting for' not in capsys.readouterr().out


def test_a_fully_posted_trip_is_left_alone() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace('+00:00', 'Z')
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", links="Pickup: u1\nDrop: u2", caption="cap",
             uploaded="TRUE", uploaded_at=recent, uploaded_fb="TRUE"),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})
    fb = FakeFacebookPublisher()

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher, fb_publisher=fb)

    assert rc == 0
    assert publisher.calls == []
    assert fb.calls == []


def test_facebook_keeps_the_caption_whole() -> None:
    """Instagram drops the trailing link once a product tag carries it. On
    Facebook that link is clickable and is the only route to the package page,
    so the caption goes out untouched."""
    caption = "Three days on the mountain.\n\n#bromo\n\nhttps://javavolcano-touroperator.com/tours/x"
    rows = rows_from_values([
        _row(no="1", booking="JVTO-9", package_code="package-SUB-3D2N-002",
             links="Pickup: u1\nDrop: u2", caption=caption, uploaded="FALSE"),
    ])
    queue = FakeQueue(rows)
    fb = FakeFacebookPublisher()

    run_post_trip(make_settings(), force=True, queue=queue,
                  publisher=FakePublisher({'status': 'published'}), fb_publisher=fb)

    _urls, message, page_name = fb.calls[0]
    assert message == caption
    assert page_name == 'Java Volcano Tour Operator'


def test_a_facebook_failure_does_not_undo_instagram(capsys) -> None:
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE"),
    ])
    queue = FakeQueue(rows)
    fb = FakeFacebookPublisher({'status': 'error', 'message': 'page token expired'})

    rc = run_post_trip(make_settings(), force=True, queue=queue,
                       publisher=FakePublisher({'status': 'published'}), fb_publisher=fb)

    assert rc == 1
    assert len(_marked(queue, 'instagram')) == 1     # Instagram stands
    assert _marked(queue, 'facebook') == []          # and Facebook retries next run
    assert 'Facebook publish failed' in capsys.readouterr().out


def test_a_named_booking_overrides_the_queue_and_the_gate(capsys) -> None:
    """Choosing a trip by hand is an operator decision: it jumps the order and
    ignores the four-day gate, even when a more recent post exists."""
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace('+00:00', 'Z')
    rows = rows_from_values([
        _row(no="1", booking="JVTO-1", links="Pickup: u1\nDrop: u2", caption="cap",
             uploaded="TRUE", uploaded_at=recent),
        _row(no="2", booking="JVTO-2", links="Pickup: a\nDrop: b", caption="cap", uploaded="FALSE"),
        _row(no="3", booking="JVTO-3", links="Pickup: c\nDrop: d", caption="cap", uploaded="FALSE"),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=False, queue=queue, publisher=publisher,
                       fb_publisher=FakeFacebookPublisher(), booking_id="JVTO-3")

    assert rc == 0
    # JVTO-2 is older and would have been chosen by the queue.
    assert [c[0].booking_id for c in queue.mark_uploaded_calls] == ['JVTO-3', 'JVTO-3']
    assert 'waiting for' not in capsys.readouterr().out


def test_a_named_booking_still_has_to_pass_the_checks(capsys) -> None:
    """Being picked by hand does not make a broken row postable."""
    rows = rows_from_values([
        _row(no="1", booking="JVTO-9", links="Pickup: only-one", caption="cap", uploaded="FALSE"),
    ])
    queue = FakeQueue(rows)
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher,
                       fb_publisher=FakeFacebookPublisher(), booking_id="JVTO-9")

    assert rc == 1
    assert publisher.calls == []
    assert 'carousel needs' in capsys.readouterr().out


def test_an_unknown_booking_is_reported_not_guessed(capsys) -> None:
    queue = FakeQueue(rows_from_values([
        _row(no="1", booking="JVTO-1", links="Pickup: u1\nDrop: u2", caption="cap", uploaded="FALSE"),
    ]))
    publisher = FakePublisher({'status': 'published'})

    rc = run_post_trip(make_settings(), force=True, queue=queue, publisher=publisher,
                       fb_publisher=FakeFacebookPublisher(), booking_id="JVTO-NOPE")

    assert rc == 1
    assert publisher.calls == []
    assert 'not in the sheet' in capsys.readouterr().out
