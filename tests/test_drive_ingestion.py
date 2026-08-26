import json

from jvto_instagram_automation.drive_ingestion import ComposioConnector


def test_coerce_file_items_supports_multiple_shapes() -> None:
    connector = ComposioConnector(api_key='test-key')
    assert connector._coerce_file_items([{'id': '1'}]) == [{'id': '1'}]
    assert connector._coerce_file_items({'files': [{'id': '2'}]}) == [{'id': '2'}]
    assert connector._coerce_file_items({'file': {'id': '3'}}) == [{'id': '3'}]
    assert connector._coerce_file_items({'unrelated': 'value'}) == []


def test_looks_like_review_file() -> None:
    connector = ComposioConnector(api_key='test-key')
    assert connector._looks_like_review_file('review-123.json') is True
    assert connector._looks_like_review_file('reviews.csv') is True
    assert connector._looks_like_review_file('random.pdf') is False
    assert connector._looks_like_review_file(None) is False


def test_parse_drive_review_payload_supports_reviews_array() -> None:
    connector = ComposioConnector(api_key='test-key')
    payload = json.dumps({'reviews': [{'comment': 'Amazing trip', 'reviewer': {'displayName': 'Ari'}}]})

    parsed = connector.parse_drive_review_payload(payload)

    assert len(parsed) == 1
    assert parsed[0]['comment'] == 'Amazing trip'


def test_invoke_tool_tries_real_action_name_before_fallback_aliases() -> None:
    connector = ComposioConnector(api_key='test-key')
    calls = []

    class FakeTools:
        def execute(self, tool_name, kwargs):
            calls.append(tool_name)
            if tool_name != 'GOOGLEDRIVE_LIST_FILES':
                raise RuntimeError('not this one')
            return {'files': [{'id': 'abc'}]}

    result = connector._invoke_tool(
        FakeTools(),
        ('GOOGLEDRIVE_LIST_FILES', 'googledrive_search_files', 'googledrive_list_files'),
        {'folder_id': 'x'},
    )

    assert result == {'files': [{'id': 'abc'}]}
    assert calls[0] == 'GOOGLEDRIVE_LIST_FILES'


def test_no_local_auth_state_file_is_created() -> None:
    # Regression guard: the old DIY OAuth-state approach wrote a JSON file
    # that wasn't reliably gitignored. Auth is Composio-CLI-managed now, so
    # no such attribute/file should exist at all.
    connector = ComposioConnector(api_key='test-key')
    assert not hasattr(connector, 'state_path')
    assert not hasattr(connector, 'save_state')
