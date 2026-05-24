"""
Quick Gmail integration test — run this once per project to verify credentials.

Usage:
    python test_gmail.py --project filmfreeway --to your@email.com
"""

import argparse
import base64
from email.mime.text import MIMEText

from config import Config
from email_sender.auth import get_gmail_credentials
from googleapiclient.discovery import build


def send_test_email(project: str, to_address: str):
    Config.load(project)
    Config.validate()

    print(f"Project  : {project}")
    print(f"Gmail ID : {Config.GMAIL_CLIENT_ID[:40]}…")
    print(f"Token    : {Config.GMAIL_TOKEN_FILE}")
    print("Authorising Gmail…")

    creds = get_gmail_credentials()
    service = build("gmail", "v1", credentials=creds)

    msg = MIMEText(
        f"Test email from Certificate Automation pipeline.\n\n"
        f"Project : {project}\n"
        f"If you received this, Gmail integration is working correctly!",
        "plain"
    )
    msg["To"] = to_address
    msg["From"] = "me"
    msg["Subject"] = f"✅ Certificate Automation [{project}] — Gmail Test"

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()

    print(f"\n✅ Test email sent!")
    print(f"   To      : {to_address}")
    print(f"   Gmail ID: {result['id']}")
    print(f"\nGmail integration is ready for project '{project}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="Project name (matches projects/<name>/.env)")
    parser.add_argument("--to", required=True, help="Email address to send test to")
    args = parser.parse_args()
    send_test_email(args.project, args.to)
