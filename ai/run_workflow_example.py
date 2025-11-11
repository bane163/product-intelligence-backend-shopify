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

    if result is None:
        print("Workflow produced no products.")
        return

    print("\n=== Products (count: {}) ===".format(len(result.products)))
    for idx, product in enumerate(result.products, start=1):
        print(f"[{idx}] {product.title}")
        if product.body_html:
            snippet = product.body_html[:120].replace("\n", " ")
            print(
                f"    body_html: {snippet}{'...' if len(product.body_html) > 120 else ''}"
            )
        if product.variants:
            print(f"    variants: {len(product.variants)}")
        if product.images:
            print(f"    images: {len(product.images)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_workflow_example.py path/to/file.xlsx")
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1]))
