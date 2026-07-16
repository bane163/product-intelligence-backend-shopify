import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from shared.observability import (
    bind_observability_context,
    resolve_request_and_correlation_ids,
)

# Import routers from the api package
from api.shopify_products import router as shopify_router
from api.agents import router as agents_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _normalize_origin(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return raw.rstrip("/")


def _resolve_allowed_origins() -> list[str]:
    configured = str(os.getenv("BACKEND_ALLOWED_ORIGINS", "")).strip()
    if configured:
        origins = [
            normalized
            for normalized in (
                _normalize_origin(item) for item in configured.split(",")
            )
            if normalized
        ]
        if origins:
            return origins

    app_url = _normalize_origin(os.getenv("SHOPIFY_APP_URL", ""))
    if app_url:
        return [app_url]

    return [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    tunnel_task: asyncio.Task[str | None] | None = None
    debug_enabled = os.getenv("DEBUG", "").lower() in ("1", "true")

    def _log_tunnel_result(task: asyncio.Task[str | None]) -> None:
        if task.cancelled():
            return
        try:
            tunnel_url = task.result()
        except Exception:
            logger.exception("Failed to start tunnel, falling back to localhost")
            return

        if tunnel_url:
            logger.info("Collabora tunnel URL: %s", tunnel_url)
        else:
            logger.warning("Failed to start tunnel, falling back to localhost")

    # Startup
    if debug_enabled:
        from cloudflare_tunnel import start_tunnel
        from services.source_link_trace import prune

        logger.info("DEBUG mode enabled, starting tunnel for Collabora...")
        asyncio.create_task(asyncio.to_thread(prune))
        tunnel_task = asyncio.create_task(start_tunnel(9980))
        tunnel_task.add_done_callback(_log_tunnel_result)

    try:
        yield
    finally:
        # Shutdown
        if tunnel_task is not None and not tunnel_task.done():
            tunnel_task.cancel()
            with suppress(asyncio.CancelledError):
                await tunnel_task
        if debug_enabled:
            from cloudflare_tunnel import stop_tunnel

            logger.info("Stopping tunnel...")
            stop_tunnel()


# FastAPI app instance — uvicorn/fastapi looks for an `app` variable by default
app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def attach_observability_context(request: Request, call_next):
    request_id, correlation_id = resolve_request_and_correlation_ids(
        request_id=request.headers.get("x-request-id"),
        correlation_id=request.headers.get("x-correlation-id"),
    )
    request.state.request_id = request_id
    request.state.correlation_id = correlation_id
    with bind_observability_context(
        request_id=request_id, correlation_id=correlation_id
    ):
        response = await call_next(request)
    response.headers["x-request-id"] = request_id
    response.headers["x-correlation-id"] = correlation_id
    return response


# CORS middleware for Shopify embedded app
app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(shopify_router)
app.include_router(agents_router)


@app.get("/health", tags=["ops"])
async def healthcheck():
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
async def readiness_check():
    from app_context import get_app_context

    get_app_context()
    from services.collabora_service import CollaboraUnavailable
    ctx = get_app_context()
    try:
        collabora = ctx.services.collabora.readiness()
    except CollaboraUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from exc
    tunnel_required = os.getenv("COLLABORA_TUNNEL_REQUIRED", "").lower() in (
        "1",
        "true",
    )
    if tunnel_required and not os.getenv("COLLABORA_PUBLIC_URL", "").strip():
        from cloudflare_tunnel import get_tunnel_url

        if not get_tunnel_url():
            raise HTTPException(
                status_code=503,
                detail="Collabora public tunnel is reconnecting",
            )
    return {
        "status": "ready",
        "services": ["supabase", "llm", "collabora", "tracing", "shopify"],
        "collabora": {key: value for key, value in collabora.items() if key != "discovery"},
    }


def main():
    # Keep existing CLI behaviour
    print("Hello from shopify-supabase-backend!")


if __name__ == "__main__":
    main()
