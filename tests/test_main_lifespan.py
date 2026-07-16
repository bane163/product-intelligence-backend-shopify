import asyncio
import os

import pytest

# Ensure env vars are present before importing the app module.
os.environ["SHOPIFY_STORE"] = "test-shop.myshopify.com"
os.environ["SHOPIFY_ACCESS_TOKEN"] = "token"

import cloudflare_tunnel
import main


@pytest.mark.asyncio
async def test_debug_lifespan_starts_tunnel_without_blocking_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    stop_calls = 0

    async def fake_start_tunnel(target_port: int) -> str | None:
        assert target_port == 9980
        started.set()
        await release.wait()
        return "https://example.trycloudflare.com"

    def fake_stop_tunnel() -> None:
        nonlocal stop_calls
        stop_calls += 1
        release.set()

    async def run_lifespan() -> None:
        async with main.lifespan(main.app):
            await asyncio.wait_for(started.wait(), timeout=0.1)

    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setattr(cloudflare_tunnel, "start_tunnel", fake_start_tunnel)
    monkeypatch.setattr(cloudflare_tunnel, "stop_tunnel", fake_stop_tunnel)

    await asyncio.wait_for(run_lifespan(), timeout=0.2)

    assert stop_calls == 1
