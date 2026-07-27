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
