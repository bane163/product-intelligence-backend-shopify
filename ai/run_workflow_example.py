import asyncio
import os
import sys

from ai.excel_workflow import run_excel_agent_workflow


async def main(path: str) -> None:
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, "rb") as fh:
        data = fh.read()

    collabora = os.getenv("COLLABORA_URL", "http://localhost:9980")
    print(f"Using Collabora: {collabora}")

    result = await run_excel_agent_workflow(data, collabora_base_url=collabora)

    print("\n=== Agent response ===")
    print(result.get("agent_response"))
    print("\n=== Extracted text preview ===")
    print((result.get("extracted_text") or "")[:200])
    print("\n=== First PNG (base64 length) ===")
    pngs = result.get("pngs_base64") or []
    print(len(pngs[0]) if pngs else "(no png)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_workflow_example.py path/to/file.xlsx")
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1]))
