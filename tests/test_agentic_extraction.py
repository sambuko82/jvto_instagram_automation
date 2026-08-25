from jvto_instagram_automation.agentic_extraction import _parse_json_payload


def test_parse_json_payload_supports_code_fences() -> None:
    raw = '```json\n{"guest_name": "Ari", "caption": "A dreamy honeymoon story", "visual_prompt": "Sunrise at Bromo"}\n```'

    payload = _parse_json_payload(raw)

    assert payload['guest_name'] == 'Ari'
    assert payload['caption'] == 'A dreamy honeymoon story'
