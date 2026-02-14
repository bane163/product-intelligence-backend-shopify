import argparse
import os
from agent_framework.observability import setup_observability
from agent_framework import get_logger

from app_context import get_ctx

logger = get_logger()


def main(
    path: str, write_to_file: bool = False, output_path: str | None = None
) -> None:
    """Run the example workflow.

    Args:
        path: Path to an Excel or CSV file on disk.
        write_to_file: If True, run the workflow end-to-end and write the
            produced Excel workbook to disk via the writer agent (if the
            agent produces one). Otherwise start the dev UI for interactive
            inspection.
        output_path: Optional path for the produced workbook when
            write_to_file=True.
    """
    setup_observability()

    if not os.path.exists(path):
        print("this is a test")
        print(f"File not found: {path}")
        return

    with open(path, "rb") as fh:
        data = fh.read()

    collabora = os.getenv("COLLABORA_URL", "http://localhost:9980")
    print(f"Using Collabora: {collabora}")

    workflow = get_ctx().services.llm.get_agent_workflow(
        data,
        collabora_base_url=collabora,
        write_to_file=write_to_file,
        output_path=output_path,
    )
    from agent_framework.devui import serve

    if workflow:
        serve(entities=[workflow], port=8093, auto_open=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the excel -> collabora -> agent workflow (dev UI by default)."
    )
    parser.add_argument("path", help="Path to an Excel (.xlsx) or CSV file")
    parser.add_argument(
        "--write-to-file",
        action="store_true",
        help="Run the workflow end-to-end and write an output workbook to disk (if agent produces one)",
    )
    parser.add_argument(
        "--output",
        "-o",
        dest="output",
        help="Optional output path for the produced workbook when --write-to-file is used",
    )

    args = parser.parse_args()

    main(args.path, write_to_file=args.write_to_file, output_path=args.output)
