import json
import tempfile
from pathlib import Path

from jvto_instagram_automation.drive_ingestion import ComposioConnector, DriveAuthState


def test_auth_state_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_path = Path(tmp_dir) / '.drive_auth.json'
        connector = ComposioConnector(api_key='test-key', project_root=Path(tmp_dir))
        connector.state_path = state_path
        state = DriveAuthState(session_id='abc', connection_request_id='req-1', redirect_url='https://example.test/auth', folder_name='JVTO Reviews')
        connector.save_state(state)
        loaded = connector.load_state()

        assert loaded is not None
        assert loaded.session_id == 'abc'
        assert loaded.folder_name == 'JVTO Reviews'


def test_parse_drive_review_payload_supports_reviews_array() -> None:
    connector = ComposioConnector(api_key='test-key')
    payload = json.dumps({'reviews': [{'comment': 'Amazing trip', 'reviewer': {'displayName': 'Ari'}}]})

    parsed = connector.parse_drive_review_payload(payload)

    assert len(parsed) == 1
    assert parsed[0]['comment'] == 'Amazing trip'
