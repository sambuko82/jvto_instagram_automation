from jvto_instagram_automation.composio_publisher import ComposioPublisher


def test_publish_carousel_dry_run_without_image_urls() -> None:
    publisher = ComposioPublisher(api_key='test-key')
    result = publisher.publish_carousel([], caption='hello')
    assert result['status'] == 'dry_run'


def test_publish_carousel_missing_api_key() -> None:
    publisher = ComposioPublisher(api_key=None)
    result = publisher.publish_carousel(['https://example.test/card1.png'], caption='hello')
    assert result['status'] == 'missing_api_key'


def test_invoke_tool_tries_real_action_name_before_fallback_aliases() -> None:
    publisher = ComposioPublisher(api_key='test-key')
    calls = []

    class FakeTools:
        def execute(self, tool_name, kwargs):
            calls.append(tool_name)
            if tool_name != 'INSTAGRAM_CREATE_CAROUSEL_CONTAINER':
                raise RuntimeError('not this one')
            return {'id': 'container-1'}

    result = publisher._invoke_tool(
        FakeTools(),
        ('INSTAGRAM_CREATE_CAROUSEL_CONTAINER', 'instagram_create_carousel_container'),
        {'ig_user_id': 'u1'},
    )

    assert result == {'id': 'container-1'}
    assert calls[0] == 'INSTAGRAM_CREATE_CAROUSEL_CONTAINER'


def test_no_local_auth_state_file_is_created() -> None:
    publisher = ComposioPublisher(api_key='test-key')
    assert not hasattr(publisher, 'state_path')
    assert not hasattr(publisher, 'save_state')


class _Acct:
    def __init__(self, id, slug, status, created_at):
        self.id = id
        self.status = status
        self.created_at = created_at
        self.toolkit = type('T', (), {'slug': slug})()


def test_relinking_instagram_leaves_the_dead_connection_behind(monkeypatch) -> None:
    """Composio reports ACTIVE until something actually calls the token, so a
    connection Meta invalidated - the account's password changed - still reads
    ACTIVE and sits beside the fresh one after relinking. Taking the first
    match would keep choosing the dead one and publishing would stay broken
    with the account visibly 'connected'."""
    import composio

    from jvto_instagram_automation.composio_publisher import ComposioPublisher

    accounts = [
        _Acct('ca_dead', 'instagram', 'ACTIVE', '2026-09-01T00:00:00Z'),
        _Acct('ca_facebook', 'facebook', 'ACTIVE', '2026-09-09T00:00:00Z'),
        _Acct('ca_fresh', 'instagram', 'ACTIVE', '2026-09-09T10:00:00Z'),
    ]

    class _Client:
        connected_accounts = type(
            'CA', (), {'list': staticmethod(lambda **kw: type('P', (), {'items': accounts})())}
        )()

    monkeypatch.setattr(composio, 'Composio', lambda **kw: type('C', (), {'client': _Client()})())

    publisher = ComposioPublisher('key', 'jvto_automation')
    assert publisher._connected_account_id() == 'ca_fresh'
