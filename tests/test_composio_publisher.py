import tempfile
from pathlib import Path

from jvto_instagram_automation.composio_publisher import ComposioPublisher, InstagramAuthState


def test_instagram_auth_state_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        publisher = ComposioPublisher(api_key='test-key', project_root=Path(tmp_dir))
        state = InstagramAuthState(session_id='abc', redirect_url='https://example.test/auth', status='pending')
        publisher.save_state(state)
        loaded = publisher.load_state()

        assert loaded is not None
        assert loaded.session_id == 'abc'
        assert loaded.redirect_url == 'https://example.test/auth'
