# Certificate Automation

Automates end-to-end certificate generation and delivery: reads recipient data from a CSV, personalises certificate designs in Canva via the API, exports them as PDFs, and emails each one using a configurable mail template.

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Project Structure](#project-structure)
- [Setup](#setup)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [CSV Format](#csv-format)
  - [Email Template](#email-template)
- [Usage](#usage)
- [How It Works](#how-it-works)
- [Canva API Setup](#canva-api-setup)
- [Email Provider Setup](#email-provider-setup)
- [Troubleshooting](#troubleshooting)

---

## Overview

```
recipients.csv  ──►  Canva API (fill template)  ──►  Export PDF  ──►  Send via Email
```

For each row in the CSV the pipeline:
1. Duplicates the master Canva design
2. Replaces text fields (name, date, course, etc.) with row values
3. Exports the design as a PDF
4. Sends the PDF as an email attachment to the recipient's address using the configured template

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.10 + |
| pip | latest |
| Canva account | Connect API enabled |
| SMTP-capable email account | Gmail, SendGrid, AWS SES, etc. |

---

## Project Structure

```
certificate-automation/
├── README.md
├── .env.example            # environment variable template (copy → .env)
├── requirements.txt        # Python dependencies
├── main.py                 # entry point — orchestrates the full pipeline
├── config.py               # loads and validates env + config
├── canva/
│   ├── client.py           # Canva Connect API wrapper
│   └── design.py           # duplicate design, update text fields, export PDF
├── email_sender/
│   ├── client.py           # SMTP / SendGrid client wrapper
│   └── templates/
│       └── certificate.html  # Jinja2 HTML email template
├── data/
│   ├── recipients.csv      # input: one recipient per row
│   └── output/             # generated PDFs land here (git-ignored)
└── tests/
    ├── test_canva.py
    └── test_email.py
```

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/your-org/certificate-automation.git
cd certificate-automation

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the env template and fill in your credentials
cp .env.example .env
```

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and set every value before running.

```dotenv
# ── Canva ─────────────────────────────────────────────────────────────────────
CANVA_CLIENT_ID=your_canva_oauth_client_id
CANVA_CLIENT_SECRET=your_canva_oauth_client_secret
CANVA_MASTER_DESIGN_ID=DAxxxxxxxxxxxxxxxx   # ID of your certificate template design

# ── Email (SMTP — e.g. Gmail) ─────────────────────────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your_app_password            # use an App Password, not your login password
EMAIL_FROM_NAME=Your Organisation
EMAIL_FROM_ADDRESS=you@gmail.com
EMAIL_SUBJECT=Your Certificate is Here!

# ── Paths ─────────────────────────────────────────────────────────────────────
CSV_PATH=data/recipients.csv
OUTPUT_DIR=data/output
EMAIL_TEMPLATE_PATH=email_sender/templates/certificate.html
```

> **Gmail users:** generate an [App Password](https://myaccount.google.com/apppasswords) (requires 2FA) and use it as `SMTP_PASSWORD`.

---

### CSV Format

Place your recipient list at `data/recipients.csv`. The file must have **headers in the first row**.

| Column | Required | Description |
|--------|----------|-------------|
| `name` | Yes | Full name printed on the certificate |
| `email` | Yes | Recipient email address |
| `course` | No | Course or programme name |
| `date` | No | Completion date (e.g. `24 May 2026`) |
| `grade` | No | Grade or distinction (e.g. `Distinction`) |

**Example:**

```csv
name,email,course,date,grade
Alice Johnson,alice@example.com,Python Fundamentals,24 May 2026,Distinction
Bob Smith,bob@example.com,Data Science Bootcamp,24 May 2026,Merit
Carol White,carol@example.com,Web Development,24 May 2026,Pass
```

Extra columns are passed as-is to the email template and can be referenced with `{{ column_name }}`.

---

### Email Template

The HTML email body lives at `email_sender/templates/certificate.html` and uses [Jinja2](https://jinja.palletsprojects.com/) syntax. Every CSV column is available as a variable.

**Default template variables:**

| Variable | Value |
|----------|-------|
| `{{ name }}` | Recipient's full name |
| `{{ course }}` | Course name |
| `{{ date }}` | Completion date |
| `{{ grade }}` | Grade / distinction |

**Minimal example template:**

```html
<!DOCTYPE html>
<html>
<body>
  <p>Dear {{ name }},</p>
  <p>
    Congratulations on successfully completing <strong>{{ course }}</strong>
    on {{ date }}{% if grade %} with <strong>{{ grade }}</strong>{% endif %}.
  </p>
  <p>Please find your certificate attached.</p>
  <p>Best regards,<br>The Team</p>
</body>
</html>
```

---

## Usage

### Run the full pipeline

```bash
python main.py
```

This processes every row in `recipients.csv`, generates a PDF certificate, and emails it.

### Dry run (generate PDFs, skip email)

```bash
python main.py --dry-run
```

### Process a single recipient (useful for testing)

```bash
python main.py --email alice@example.com
```

### Specify a custom CSV path

```bash
python main.py --csv path/to/other.csv
```

All generated PDFs are saved to `data/output/<name>_certificate.pdf`.

---

## How It Works

```
main.py
  │
  ├─ 1. Read CSV  →  list of recipient dicts
  │
  ├─ 2. For each recipient:
  │       a. canva/design.py  →  duplicate master design
  │       b. canva/design.py  →  update text fields with recipient data
  │       c. canva/design.py  →  export design as PDF → data/output/
  │       d. email_sender/client.py  →  render HTML template (Jinja2)
  │       e. email_sender/client.py  →  attach PDF + send via SMTP
  │
  └─ 3. Print summary (sent / skipped / failed)
```

---

## Canva API Setup

1. Go to [Canva Developers](https://www.canva.com/developers/) and create an integration.
2. Enable the **Canva Connect API** and note your **Client ID** and **Client Secret**.
3. In your Canva account, create your certificate design (set up text fields for name, course, date, etc.).
4. Copy the design ID from the URL:
   ```
   https://www.canva.com/design/DAxxxxxxxxxxxxxxxx/edit
                                  ^^^^^^^^^^^^^^^^^^
                                  this is CANVA_MASTER_DESIGN_ID
   ```
5. Set `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, and `CANVA_MASTER_DESIGN_ID` in `.env`.

> The pipeline duplicates the master design for each recipient so the original is never modified.

---

## Email Provider Setup

### Gmail (recommended for small volumes)

1. Enable 2-Step Verification on your Google account.
2. Generate an [App Password](https://myaccount.google.com/apppasswords) for "Mail".
3. Use:
   ```dotenv
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=you@gmail.com
   SMTP_PASSWORD=<app_password>
   ```

### SendGrid

Replace the SMTP block in `.env` with:

```dotenv
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USERNAME=apikey
SMTP_PASSWORD=<your_sendgrid_api_key>
```

### AWS SES

```dotenv
SMTP_HOST=email-smtp.<region>.amazonaws.com
SMTP_PORT=587
SMTP_USERNAME=<ses_smtp_username>
SMTP_PASSWORD=<ses_smtp_password>
```

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---------|-------------|-----|
| `AuthenticationError` from Canva | Invalid or expired OAuth token | Re-run the OAuth flow; check `CANVA_CLIENT_ID` / `CANVA_CLIENT_SECRET` |
| `SMTPAuthenticationError` | Wrong SMTP credentials | For Gmail use an App Password, not your login password |
| PDF not generated | Canva export timeout | Retry or increase `EXPORT_POLL_TIMEOUT` in `config.py` |
| Email sent but no attachment | Output PDF path mismatch | Check that `OUTPUT_DIR` is writable and the PDF exists after export |
| Some rows skipped | Missing required `email` field | Ensure every row has a non-empty `email` column |
| Rate-limited by Canva | Too many designs duplicated quickly | Add `CANVA_REQUEST_DELAY_SECONDS` (e.g. `1`) to `.env` |
