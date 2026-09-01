"""A refused product tag must never cost the trip its post.

Tagging depends on things outside this program's control - a Meta shop
connection, a catalog entry, an approval status, Meta's own uptime. The crew
spent a day producing the photos; losing the whole carousel because a garnish
could not be attached would be the wrong trade every time.
"""

from jvto_instagram_automation.composio_publisher import ComposioPublisher
from jvto_instagram_automation.sheet_queue import rows_from_values


def _row(no="1", booking="JVTO-1", customer="Cust", package="P", package_code="",
         crew="C", instagram="", listed_by="Boy", links="", caption="cap",
         uploaded="FALSE", uploaded_at=""):
    return [no, booking, customer, package, package_code, crew, instagram,
            listed_by, links, caption, uploaded, uploaded_at]


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

    children, reason = publisher._product_tagged_children('ig', ['u1'], 'package-X')

    assert children is None
    assert 'no Meta shop connection' in reason


def test_a_package_absent_from_the_catalog_skips_the_tag_without_raising():
    publisher = _publisher()
    publisher._shopping_account_id = lambda: 'ca_1'
    publisher._business_account_id = lambda account: 'ig_business'
    publisher._product_id_for = lambda account, ig, code: None

    children, reason = publisher._product_tagged_children('ig', ['u1'], 'package-GHOST')

    assert children is None
    assert 'package-GHOST is not in the shop catalog' in reason


def test_a_meta_outage_mid_lookup_skips_the_tag_without_raising():
    def explode(*args, **kwargs):
        raise RuntimeError('Meta said no')

    publisher = _publisher()
    publisher._shopping_account_id = lambda: 'ca_1'
    publisher._business_account_id = lambda account: 'ig_business'
    publisher._product_id_for = explode

    children, reason = publisher._product_tagged_children('ig', ['u1'], 'package-X')

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
    publisher._product_id_for = lambda account, ig, code: '123'
    publisher._tagged_children = explode

    children, reason = publisher._product_tagged_children('ig', ['u1'], 'package-X')

    assert children is None
    assert reason == 'Cannot tag product'


def test_the_happy_path_returns_children_and_no_reason():
    publisher = _publisher()
    publisher._shopping_account_id = lambda: 'ca_1'
    publisher._business_account_id = lambda account: 'ig_business'
    publisher._product_id_for = lambda account, ig, code: '123'
    publisher._tagged_children = lambda account, ig, urls, pid: ['c1', 'c2']

    children, reason = publisher._product_tagged_children('ig', ['u1', 'u2'], 'package-X')

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

    children, reason = publisher._product_tagged_children('ig', ['u1'], 'package-X')

    assert children is None
    assert 'no Instagram business account' in reason
