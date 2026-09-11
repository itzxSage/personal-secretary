"""The scanner must detect credentials without reproducing their values."""

import json

from scripts.scan_secrets import findings_for_text


def test_secret_findings_never_include_credential_values() -> None:
    credential = "AK" + "IA" + "Z" * 16
    result = findings_for_text("configuration\n" + credential)
    assert result == [{"rule": "aws-access-key", "line": 2}]
    assert credential not in json.dumps(result)


def test_private_key_and_provider_tokens_are_detected() -> None:
    assert findings_for_text("-----BEGIN " + "PRIVATE KEY-----")
    assert findings_for_text("sk-" + "proj-" + "A" * 48)
    assert findings_for_text("gh" + "p_" + "A" * 40)
    assert findings_for_text("fixture-token") == []
