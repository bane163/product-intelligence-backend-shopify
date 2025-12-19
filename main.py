from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from auth import router as auth_router

# Import routers from the routes package
from routes.shopify_products import router as shopify_router
from routes.maf import router as microsoft_router
from routes.agents import router as agents_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    # Startup
    if os.getenv("DEBUG", "").lower() in ("1", "true"):
        from cloudflare_tunnel import start_tunnel
        logger.info("DEBUG mode enabled, starting cloudflared tunnel for Collabora...")
        tunnel_url = await start_tunnel(9980)
        if tunnel_url:
            logger.info(f"Collabora tunnel URL: {tunnel_url}")
        else:
            logger.warning("Failed to start cloudflared tunnel, falling back to localhost")
    
    yield
    
    # Shutdown
    if os.getenv("DEBUG", "").lower() in ("1", "true"):
        from cloudflare_tunnel import stop_tunnel
        logger.info("Stopping cloudflared tunnel...")
        stop_tunnel()


# FastAPI app instance — uvicorn/fastapi looks for an `app` variable by default
app = FastAPI(lifespan=lifespan)

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
app.include_router(microsoft_router)
app.include_router(agents_router)


def main():
    # Keep existing CLI behaviour
    print("Hello from shopify-supabase-backend!")


if __name__ == "__main__":
    main()
