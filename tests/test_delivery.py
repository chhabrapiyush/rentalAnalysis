from pathlib import Path

import pytest

from rentalanalysis.delivery import EmailConfigError, build_workbook_email, send_workbook_email


def test_build_email_has_attachment(tmp_path):
    f = tmp_path / "deals.xlsx"
    f.write_bytes(b"PK\x03\x04 fake xlsx bytes")
    msg = build_workbook_email(f, "me@example.com", "bot@example.com")

    assert msg["To"] == "me@example.com"
    assert msg["From"] == "bot@example.com"
    assert "deals.xlsx" in msg["Subject"]

    attachments = [p for p in msg.iter_attachments()]
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "deals.xlsx"


def test_send_without_smtp_config_raises(tmp_path, monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    f = tmp_path / "deals.xlsx"
    f.write_bytes(b"x")
    with pytest.raises(EmailConfigError):
        send_workbook_email(f, "me@example.com")
