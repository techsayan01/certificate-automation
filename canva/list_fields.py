"""
List the placeholder field names defined in your Canva brand template.

Usage:
    python canva/list_fields.py --project cinema_na_santa

This will open your browser to authorise with Canva (first run only),
then print the field names you need to set in .env:

    CANVA_NAME_FIELD     = <whatever the name field is called>
    CANVA_PROJECT_FIELD  = <whatever the project field is called>
    CANVA_CATEGORY_FIELD = <whatever the category field is called>
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config
from canva.client import CanvaClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True,
                        help="Project name (e.g. cinema_na_santa)")
    args = parser.parse_args()

    Config.load(args.project)

    print(f"\nFetching fields for brand template: {Config.CANVA_BRAND_TEMPLATE_ID}\n")

    client = CanvaClient()
    fields = client.list_template_fields(Config.CANVA_BRAND_TEMPLATE_ID)

    if not fields:
        print("No fields found — make sure your template has text placeholder fields.")
        print("In Canva: click a text element → ••• menu → 'Add to brand template data'")
        sys.exit(1)

    print("Fields found in your brand template:")
    print("-" * 40)
    for f in fields:
        print(f"  {f}")
    print("-" * 40)
    print()
    print("Update your root .env accordingly:")
    print(f"  CANVA_NAME_FIELD     = {fields[0] if len(fields) > 0 else 'Name'}")
    print(f"  CANVA_PROJECT_FIELD  = {fields[1] if len(fields) > 1 else 'Project'}")
    print(f"  CANVA_CATEGORY_FIELD = {fields[2] if len(fields) > 2 else 'Category'}")
    print()


if __name__ == "__main__":
    main()
