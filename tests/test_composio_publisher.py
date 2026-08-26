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
