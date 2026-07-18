#!/usr/bin/env python3
"""Non-secret Azure layout access diagnostic for one PDF."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from infrastructure.adapters.azure_document_layout_adapter import AzureDocumentLayoutAdapter


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    endpoint = os.getenv("DOCUMENTINTELLIGENCE_ENDPOINT", "").strip()
    key = os.getenv("DOCUMENTINTELLIGENCE_API_KEY", "").strip()
    if not endpoint or not key:
        raise SystemExit("Set both DOCUMENTINTELLIGENCE_ENDPOINT and DOCUMENTINTELLIGENCE_API_KEY")
    layout = await AzureDocumentLayoutAdapter(endpoint, key).analyze_pdf(
        args.pdf.read_bytes(), args.pdf.name
    )
    print(f"provider={layout.provider} pages={layout.page_count} covered={len(layout.covered_pages)} anchors={len(layout.anchors)}")


if __name__ == "__main__":
    asyncio.run(main())
