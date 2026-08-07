"""Tests for workbook delivery channels (no network)."""
from pathlib import Path

import pytest

from rentalanalysis import delivery
from rentalanalysis.delivery import (
    GSheetsConfigError,
    _gsheet_upload_body,
    upload_workbook_to_gsheets,
)


def test_gsheet_upload_body_converts_to_native_sheet(tmp_path):
    path = tmp_path / "deal_analysis.xlsx"
    body = _gsheet_upload_body(path, folder_id=None, title=None)
    assert body["mimeType"] == "application/vnd.google-apps.spreadsheet"
    assert body["name"] == "deal_analysis"       # defaults to file stem
    assert "parents" not in body


def test_gsheet_upload_body_uses_title_and_folder(tmp_path):
    path = tmp_path / "deal_analysis.xlsx"
    body = _gsheet_upload_body(path, folder_id="FOLDER123", title="My Deals")
    assert body["name"] == "My Deals"
    assert body["parents"] == ["FOLDER123"]


def test_gsheet_upload_builds_correct_request(tmp_path, mocker):
    xlsx = tmp_path / "out.xlsx"
    xlsx.write_bytes(b"fake xlsx bytes")

    # Skip real auth: hand back a sentinel creds object.
    mocker.patch.object(delivery, "_load_gsheets_credentials", return_value=object())

    created = {"id": "abc123", "webViewLink": "https://docs.google.com/spreadsheets/d/abc123"}
    files_mock = mocker.MagicMock()
    files_mock.create.return_value.execute.return_value = created
    service_mock = mocker.MagicMock()
    service_mock.files.return_value = files_mock

    # The googleapiclient imports are lazy inside the function — patch them there.
    mocker.patch("googleapiclient.discovery.build", return_value=service_mock)
    mocker.patch("googleapiclient.http.MediaFileUpload", return_value="MEDIA")

    url = upload_workbook_to_gsheets(xlsx, folder_id="F1", title="T")

    assert url == created["webViewLink"]
    _, kwargs = files_mock.create.call_args
    assert kwargs["body"]["mimeType"] == "application/vnd.google-apps.spreadsheet"
    assert kwargs["body"]["parents"] == ["F1"]
    assert kwargs["fields"] == "id,webViewLink"


def test_gsheet_upload_requires_folder(tmp_path):
    # folder_id is mandatory (service accounts have no personal Drive).
    with pytest.raises(GSheetsConfigError):
        upload_workbook_to_gsheets(tmp_path / "out.xlsx", folder_id="")


def test_gsheet_missing_service_account_env_raises(tmp_path, monkeypatch):
    # No GOOGLE_APPLICATION_CREDENTIALS set → clear config error (folder is provided).
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(GSheetsConfigError):
        upload_workbook_to_gsheets(tmp_path / "out.xlsx", folder_id="F1")


def test_gsheet_uses_service_account_when_configured(tmp_path, monkeypatch, mocker):
    # A service-account key path is set → SA creds are loaded with the drive.file scope.
    sa_file = tmp_path / "sa.json"
    sa_file.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(sa_file))

    sentinel = object()
    from_file = mocker.patch(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        return_value=sentinel,
    )
    creds = delivery._load_gsheets_credentials()

    assert creds is sentinel
    from_file.assert_called_once()
    # scope stays least-privilege drive.file
    _, kwargs = from_file.call_args
    assert kwargs["scopes"] == ["https://www.googleapis.com/auth/drive.file"]


def test_gsheet_service_account_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing.json"))
    with pytest.raises(GSheetsConfigError):
        delivery._load_gsheets_credentials()
