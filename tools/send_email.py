import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
# Where alerts are sent. Defaults to the sending account if EMAIL_TO is unset.
DEFAULT_TO = os.getenv("EMAIL_TO") or GMAIL_USER


def send_email(subject, body, to=None, html=False):
    to = to or DEFAULT_TO
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "GMAIL_USER / GMAIL_APP_PASSWORD not set in .env. "
            "Generate an app password at: Google Account → Security → 2-Step Verification → App Passwords"
        )
    if not to:
        raise RuntimeError("No recipient: set EMAIL_TO in .env (or pass to=...).")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = to
    mime_type = "html" if html else "plain"
    msg.attach(MIMEText(body, mime_type))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, to, msg.as_string())
    print(f"Email sent: {subject}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python tools/send_email.py 'Subject' 'Body text'")
        sys.exit(1)
    send_email(subject=sys.argv[1], body=sys.argv[2])
