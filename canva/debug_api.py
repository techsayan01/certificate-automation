"""
Debug helper — probes Canva API endpoints to find what's available
and whether the brand template ID is correct.

Usage:
    python canva/debug_api.py --project GVFF
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from config import Config
from canva.auth import get_canva_token


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()

    Config.load(args.project)
    token = get_canva_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    base = Config.CANVA_API_BASE

    print(f"\nBase URL : {base}")
    print(f"Template : {Config.CANVA_BRAND_TEMPLATE_ID}\n")

    # 1 — list brand templates (tells us if the template ID is valid)
    print("── GET /brand-templates ──────────────────────────────")
    r = requests.get(f"{base}/brand-templates", headers=headers)
    print(f"Status: {r.status_code}")
    print(r.text[:800])
    print()

    # 2 — get the specific template
    tid = Config.CANVA_BRAND_TEMPLATE_ID
    print(f"── GET /brand-templates/{tid} ───────────────────────")
    r = requests.get(f"{base}/brand-templates/{tid}", headers=headers)
    print(f"Status: {r.status_code}")
    print(r.text[:800])
    print()

    # 3 — get the template dataset (fields)
    print(f"── GET /brand-templates/{tid}/dataset ───────────────")
    r = requests.get(f"{base}/brand-templates/{tid}/dataset", headers=headers)
    print(f"Status: {r.status_code}")
    print(r.text[:800])
    print()

    # 4 — try POST /autofills (original path, now with correct base URL)
    print("── POST /autofills ───────────────────────────────────")
    r = requests.post(
        f"{base}/autofills",
        headers=headers,
        json={
            "brand_template_id": tid,
            "title": "test",
            "data": {"Name": {"type": "text", "text": "Test"}},
        },
    )
    print(f"Status: {r.status_code}")
    print(r.text[:800])
    print()


if __name__ == "__main__":
    main()
