"""
Quick Gmail integration test — run this once to verify credentials.

Usage:
    python test_gmail.py --to your@email.com

What it does:
  1. Opens your browser to authorize Gmail (first run only).
  2. Saves gmail_token.json for future runs.
  3. Sends a plain test email to --to address.
"""

import argparse
import base64
from email.mime.text import MIMEText

from email_sender.auth import get_gmail_credentials
from googleapiclient.discovery import build


def send_test_email(to_address: str):
    print("Authorising Gmail...")
    creds = get_gmail_credentials()
    service = build("gmail", "v1", credentials=creds)

    msg = MIMEText(
        "This is a test email from your Certificate Automation pipeline.\n\n"
        "If you received this, Gmail integration is working correctly!",
        "plain"
    )
    msg["To"] = to_address
    msg["From"] = "me"
    msg["Subject"] = "✅ Certificate Automation — Gmail Test"

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()

    print(f"\n✅ Test email sent successfully!")
    print(f"   To      : {to_address}")
    print(f"   Gmail ID: {result['id']}")
    print(f"\nGmail integration is ready. You can now run the full pipeline.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="Email address to send test to")
    args = parser.parse_args()
    send_test_email(args.to)
