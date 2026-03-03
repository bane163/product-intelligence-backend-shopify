from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from auth import router as auth_router
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    # Startup
    if os.getenv("DEBUG", "").lower() in ("1", "true"):
        from cloudflare_tunnel import start_tunnel
        logger.info("DEBUG mode enabled, starting tunnel for Collabora...")
        tunnel_url = await start_tunnel(9980)
        if tunnel_url:
            logger.info(f"Collabora tunnel URL: {tunnel_url}")
        else:
            logger.warning("Failed to start tunnel, falling back to localhost")
    
    yield
    
    # Shutdown
    if os.getenv("DEBUG", "").lower() in ("1", "true"):
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
    allow_origins=["*"],  # In production, restrict to Shopify domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(shopify_router)
app.include_router(auth_router)
app.include_router(agents_router)


def main():
    # Keep existing CLI behaviour
    print("Hello from shopify-supabase-backend!")


if __name__ == "__main__":
    main()
