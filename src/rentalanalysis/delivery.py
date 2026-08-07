"""Deliver the generated workbook by email so runs can be triggered from mobile.

Uses only the Python standard library. SMTP settings come from environment
variables (typically the same .env used for OneHome credentials):

    SMTP_HOST       e.g. smtp.gmail.com
    SMTP_PORT       default 587 (STARTTLS)
    SMTP_USER       login username / from address
    SMTP_PASSWORD   password or app-specific password
    EMAIL_FROM      optional; defaults to SMTP_USER
"""
from __future__ import annotations

import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


class EmailConfigError(RuntimeError):
    pass


class GSheetsConfigError(RuntimeError):
    pass


# App only touches files it creates — least-privilege Drive scope.
_GSHEETS_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_GSHEET_MIMETYPE = "application/vnd.google-apps.spreadsheet"


def _gsheet_upload_body(path: Path, folder_id: str | None, title: str | None) -> dict:
    """Assemble the Drive files.create body — split out for testability."""
    body = {"name": title or path.stem, "mimeType": _GSHEET_MIMETYPE}
    if folder_id:
        body["parents"] = [folder_id]
    return body


def _load_gsheets_credentials():
    """Service-account credentials for Drive — the single auth path (local and CI).

    Requires GOOGLE_APPLICATION_CREDENTIALS to point at the service-account JSON key.
    No browser, no token expiry, identical behaviour everywhere.
    """
    sa_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not sa_path:
        raise GSheetsConfigError(
            "Google Sheets upload needs a service account. Set GOOGLE_APPLICATION_CREDENTIALS "
            "to the path of a service-account JSON key (Drive API enabled)."
        )
    if not Path(sa_path).exists():
        raise GSheetsConfigError(
            f"GOOGLE_APPLICATION_CREDENTIALS points to {sa_path}, which does not exist."
        )
    try:
        from google.oauth2 import service_account
    except ImportError as exc:  # optional extra not installed
        raise GSheetsConfigError(
            "Google Sheets upload requires extra deps. Install with: "
            "pip install -e '.[gsheets]'"
        ) from exc
    return service_account.Credentials.from_service_account_file(sa_path, scopes=_GSHEETS_SCOPES)


def upload_workbook_to_gsheets(path: Path, folder_id: str, title: str | None = None) -> str:
    """Upload the .xlsx to Google Drive, converting it to a native Google Sheet.

    Returns the shareable webViewLink. Authenticates as a service account via
    GOOGLE_APPLICATION_CREDENTIALS (see _load_gsheets_credentials). A service account has
    no usable My Drive of its own, so ``folder_id`` is required: create a Drive folder,
    share it (Editor) with the service account's email, and pass its ID here so the
    created Sheet lands somewhere you can see it.
    """
    if not folder_id:
        raise GSheetsConfigError(
            "A target Drive folder is required. Pass --gsheet-folder <ID> (or set "
            "GSHEETS_FOLDER_ID) — a folder you've shared with the service account's email. "
            "Service accounts have no personal Drive, so a Sheet created without a shared "
            "parent folder can be inaccessible."
        )
    creds = _load_gsheets_credentials()
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise GSheetsConfigError(
            "Google Sheets upload requires extra deps. Install with: "
            "pip install -e '.[gsheets]'"
        ) from exc

    service = build("drive", "v3", credentials=creds)
    media = MediaFileUpload(str(path), mimetype=_XLSX_MIMETYPE, resumable=False)
    created = (
        service.files()
        .create(
            body=_gsheet_upload_body(path, folder_id, title),
            media_body=media,
            fields="id,webViewLink",
        )
        .execute()
    )
    return created["webViewLink"]


def build_workbook_email(path: Path, to_addr: str, from_addr: str, subject: str | None = None) -> EmailMessage:
    """Construct the email (with the .xlsx attached) — separated for testability."""
    msg = EmailMessage()
    msg["Subject"] = subject or f"Rental analysis — {path.name}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(
        "Your rental investment analysis is attached.\n\n"
        f"File: {path.name}\n"
        "Open in Excel/Google Sheets — the Overview tab compares every property; "
        "each property has its own IRE proforma sheet with live formulas."
    )

    data = path.read_bytes()
    ctype, _ = mimetypes.guess_type(path.name)
    maintype, subtype = (ctype.split("/", 1) if ctype else ("application", "octet-stream"))
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def send_workbook_email(path: Path, to_addr: str) -> None:
    """Email the workbook using SMTP settings from the environment."""
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    if not (host and user and password):
        raise EmailConfigError(
            "Email requested but SMTP is not configured. Set SMTP_HOST, SMTP_USER, "
            "and SMTP_PASSWORD (and optionally SMTP_PORT, EMAIL_FROM) in your .env."
        )
    port = int(os.getenv("SMTP_PORT", "587"))
    from_addr = os.getenv("EMAIL_FROM", user)

    msg = build_workbook_email(path, to_addr, from_addr)

    with smtplib.SMTP(host, port, timeout=60) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
