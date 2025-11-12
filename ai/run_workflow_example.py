import asyncio
import os
import sys

from .excel_workflow import get_agent_workflow, run_excel_agent_workflow


def main(path: str) -> None:
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, "rb") as fh:
        data = fh.read()

    collabora = os.getenv("COLLABORA_URL", "http://localhost:9980")
    print(f"Using Collabora: {collabora}")

    workflow = get_agent_workflow(data, collabora_base_url=collabora)
    from agent_framework.devui import serve

    if workflow:
        serve(entities=[workflow], port=8093, auto_open=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_workflow_example.py path/to/file.xlsx")
        raise SystemExit(2)
    main(sys.argv[1])
