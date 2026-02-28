import argparse
import asyncio
import logging
import os

from application.services.offload_worker import OffloadWorker


def _configure_logging() -> None:
    configured_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, configured_level, logging.INFO)
    logging.basicConfig(level=level)


async def _run(*, once: bool) -> None:
    worker = OffloadWorker.from_env()
    if once:
        await worker.run_once()
        return
    await worker.run_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the durable offload queue worker.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim and process a single queued job, then exit.",
    )
    args = parser.parse_args()

    _configure_logging()
    asyncio.run(_run(once=args.once))


if __name__ == "__main__":
    main()
