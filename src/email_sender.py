from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_digest(
    subject: str,
    html_body: str,
    to_addr: str,
    auth_code: str,
    smtp_server: str = "smtp.qq.com",
    smtp_port: int = 465,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = to_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(to_addr, auth_code)
            server.sendmail(to_addr, [to_addr], msg.as_string())
        logger.info("Email sent to %s", to_addr)
    except Exception:
        logger.exception("Failed to send email")
