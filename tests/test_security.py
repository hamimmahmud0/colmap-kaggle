from dji_recon.util import redact, redact_text


def test_redaction_removes_secret_keys_and_mega_fragments():
    value = {"password": "unsafe", "nested": {"oauth_token": "token-value"}, "safe": "ok"}
    result = redact(value)
    assert result["password"] == "[REDACTED]"
    assert result["nested"]["oauth_token"] == "[REDACTED]"
    assert result["safe"] == "ok"
    text = redact_text("source=https://mega.nz/folder/example#private-key")
    assert "private-key" not in text
