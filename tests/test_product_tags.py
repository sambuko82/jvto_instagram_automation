"""A refused product tag must never cost the trip its post.

Tagging depends on things outside this program's control - a Meta shop
connection, a catalog entry, an approval status, Meta's own uptime. The crew
spent a day producing the photos; losing the whole carousel because a garnish
could not be attached would be the wrong trade every time.
"""

from jvto_instagram_automation.composio_publisher import ComposioPublisher, drop_trailing_link
from jvto_instagram_automation.sheet_queue import rows_from_values


def _row(no="1", booking="JVTO-1", customer="Cust", package="P", package_code="",
         crew="C", instagram="", listed_by="Boy", links="", caption="cap",
         uploaded="FALSE", uploaded_at="", uploaded_fb=None, uploaded_at_fb=""):
    # Facebook mirrors Instagram unless a test says otherwise, so existing
    # fixtures keep meaning "posted" or "not posted" rather than accidentally
    # becoming half-finished rows.
    if uploaded_fb is None:
        uploaded_fb = uploaded

    return [no, booking, customer, package, package_code, crew, instagram,
            listed_by, links, caption, uploaded, uploaded_at,
            uploaded_fb, uploaded_at_fb]


def _publisher() -> ComposioPublisher:
    return ComposioPublisher(api_key='k', user_id='u')


def test_the_sheet_s_no_code_sentinel_reads_as_no_code():
    """The crew portal writes '-' when no product applies. Passing that string
    through would send Meta looking for a package literally named '-'."""
    row = rows_from_values([_row(package_code="-")])[0]

    assert row.package_code == ''


def test_a_real_code_survives_unchanged():
    row = rows_from_values([_row(package_code="package-SUB-3D2N-003")])[0]

    assert row.package_code == 'package-SUB-3D2N-003'


def test_no_shop_connection_skips_the_tag_without_raising():
    publisher = _publisher()
    publisher._shopping_account_id = lambda: None

    children, reason, _url = publisher._product_tagged_children('ig', ['u1'], 'package-X')

    assert children is None
    assert 'no Meta shop connection' in reason


def test_a_package_absent_from_the_catalog_skips_the_tag_without_raising():
    publisher = _publisher()
    publisher._shopping_account_id = lambda: 'ca_1'
    publisher._business_account_id = lambda account: 'ig_business'
    publisher._product_for = lambda account, ig, code: None

    children, reason, _url = publisher._product_tagged_children('ig', ['u1'], 'package-GHOST')

    assert children is None
    assert 'package-GHOST is not in the shop catalog' in reason


def test_a_meta_outage_mid_lookup_skips_the_tag_without_raising():
    def explode(*args, **kwargs):
        raise RuntimeError('Meta said no')

    publisher = _publisher()
    publisher._shopping_account_id = lambda: 'ca_1'
    publisher._business_account_id = lambda account: 'ig_business'
    publisher._product_for = explode

    children, reason, _url = publisher._product_tagged_children('ig', ['u1'], 'package-X')

    assert children is None
    assert reason == 'Meta said no'


def test_a_container_refused_at_the_last_step_skips_the_tag_without_raising():
    """The lookup can succeed and the tagged container still be refused - an
    unapproved product, a revoked scope. That is the latest possible failure
    and still must not reach the caller as an exception."""
    def explode(*args, **kwargs):
        raise RuntimeError('Cannot tag product')

    publisher = _publisher()
    publisher._shopping_account_id = lambda: 'ca_1'
    publisher._business_account_id = lambda account: 'ig_business'
    publisher._product_for = lambda account, ig, code: ('123', 'https://x.test/p')
    publisher._tagged_children = explode

    children, reason, _url = publisher._product_tagged_children('ig', ['u1'], 'package-X')

    assert children is None
    assert reason == 'Cannot tag product'


def test_the_happy_path_returns_children_and_no_reason():
    publisher = _publisher()
    publisher._shopping_account_id = lambda: 'ca_1'
    publisher._business_account_id = lambda account: 'ig_business'
    publisher._product_for = lambda account, ig, code: ('123', 'https://x.test/p')
    publisher._tagged_children = lambda account, ig, urls, pid: ['c1', 'c2']

    children, reason, _url = publisher._product_tagged_children('ig', ['u1', 'u2'], 'package-X')

    assert children == ['c1', 'c2']
    assert reason is None


def test_an_unreachable_business_account_skips_the_tag_without_raising():
    """The shopping edges hang off the Instagram *business* account id, which
    is a different number from the one the publish path posts with. If that
    lookup fails, the post still has to go out."""
    def explode(account):
        raise RuntimeError('no Instagram business account is linked to these Pages')

    publisher = _publisher()
    publisher._shopping_account_id = lambda: 'ca_1'
    publisher._business_account_id = explode

    children, reason, _url = publisher._product_tagged_children('ig', ['u1'], 'package-X')

    assert children is None
    assert 'no Instagram business account' in reason


THROTTLED = 'Only photo or video can be accepted as media type'


def test_a_throttled_fetch_is_retried_rather_than_surrendered():
    """The host answers 429 under burst load and Meta reports it as a bad
    media type. Both container paths must ride that out - the first version
    guarded only the untagged fallback, and a real post lost its product tag
    to a hiccup the fallback then survived two retries later."""
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError(f'Instagram API rejected the media URL. Error: {THROTTLED}.')
        return 'container-1'

    publisher = _publisher()
    result = publisher._waiting_out_throttling('u1', flaky, backoff_seconds=0)

    assert result == 'container-1'
    assert len(calls) == 3


def test_a_genuine_rejection_is_not_retried():
    """Retrying a broken URL or a revoked token only delays the same answer."""
    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError('The image URL returned 404')

    publisher = _publisher()

    try:
        publisher._waiting_out_throttling('u1', broken, backoff_seconds=0)
        raise AssertionError('should have raised')
    except RuntimeError as exc:
        assert '404' in str(exc)

    assert len(calls) == 1


def test_throttling_that_never_clears_still_gives_up():
    """Otherwise the run hangs instead of falling through to an untagged post."""
    calls = []

    def always_throttled():
        calls.append(1)
        raise RuntimeError(f'Instagram API rejected the media URL. Error: {THROTTLED}.')

    publisher = _publisher()

    try:
        publisher._waiting_out_throttling('u1', always_throttled, attempts=3, backoff_seconds=0)
        raise AssertionError('should have raised')
    except RuntimeError as exc:
        assert THROTTLED in str(exc)

    assert len(calls) == 3


URL = 'https://javavolcano-touroperator.com/tours/from-surabaya/ijen-bromo-madakaripura-3d2n'


def test_the_link_is_dropped_when_it_is_the_final_line():
    caption = f"We ran this one last week.\n\n#bromo #ijen\n\n{URL}"

    assert drop_trailing_link(caption, URL) == "We ran this one last week.\n\n#bromo #ijen"


def test_a_doubled_slash_still_counts_as_the_same_link():
    """The crew portal builds the caption's link by concatenation, so a slug
    that already starts with '/' produces 'example.com//tours/...'. That is the
    same page; a stray character must not leave a redundant link behind."""
    caption = f"Trip report.\n\n#bromo\n\nhttps://javavolcano-touroperator.com//tours/from-surabaya/ijen-bromo-madakaripura-3d2n"

    assert drop_trailing_link(caption, URL) == "Trip report.\n\n#bromo"


def test_a_caption_without_the_link_is_returned_untouched():
    caption = "We ran this one last week.\n\n#bromo #ijen"

    assert drop_trailing_link(caption, URL) == caption


def test_a_different_link_is_left_alone():
    """Only the tagged product's own link is redundant. Anything else the
    caption ends with is content nobody asked us to remove."""
    caption = "Book by WhatsApp.\n\n#bromo\n\nhttps://wa.me/6281234567890"

    assert drop_trailing_link(caption, URL) == caption


def test_the_link_is_kept_when_it_is_not_the_last_line():
    """A URL in the middle is part of the prose, not the trailing CTA."""
    caption = f"See {URL} for dates.\n\n#bromo"

    assert drop_trailing_link(caption, URL) == caption


def test_no_product_url_means_no_surgery():
    caption = f"Trip report.\n\n{URL}"

    assert drop_trailing_link(caption, '') == caption
