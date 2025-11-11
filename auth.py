import hashlib
import hmac as _hmac
import secrets
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request

import token_store
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/shopify/auth", tags=["shopify_auth"])

# In-memory state store for CSRF protection (simple; not persistent)
_state_store: Dict[str, str] = {}


def _get_client_credentials() -> tuple[str, str]:
    import os

    client_id = os.getenv("SHOPIFY_API_KEY") or os.getenv("SHOPIFY_CLIENT_ID")
    client_secret = os.getenv("SHOPIFY_API_SECRET") or os.getenv(
        "SHOPIFY_CLIENT_SECRET"
    )
    if not client_id or not client_secret:
        raise RuntimeError(
            "SHOPIFY_API_KEY and SHOPIFY_API_SECRET must be set in environment"
        )
    return client_id, client_secret


@router.get("/install")
async def install(
    shop: str, request: Request, scope: Optional[str] = "write_products,read_products"
):
    """Generate a Shopify install (authorize) URL for the given shop and return it as a redirect target.

    Example: GET /shopify/auth/install?shop=example.myshopify.com
    """
    client_id, _ = _get_client_credentials()
    state = secrets.token_urlsafe(16)
    _state_store[state] = shop

    # Build redirect/callback url dynamically from the incoming request
    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/shopify/auth/callback"

    install_url = (
        f"https://{shop}/admin/oauth/authorize"
        f"?client_id={client_id}"
        f"&scope={scope}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return {"install_url": install_url}


def _verify_hmac(params: Dict[str, str], client_secret: str) -> bool:
    # Remove hmac param then build message per Shopify docs
    params_to_sign = {
        k: v for k, v in params.items() if k != "hmac" and k != "signature"
    }
    message = "&".join(f"{k}={params_to_sign[k]}" for k in sorted(params_to_sign))
    digest = _hmac.new(
        client_secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return _hmac.compare_digest(digest, params.get("hmac", ""))


@router.get("/callback")
async def callback(request: Request):
    """Callback endpoint Shopify redirects to after auth. Exchanges code for access token and stores it."""
    params = dict(request.query_params)
    shop = params.get("shop")
    code = params.get("code")
    state = params.get("state")

    if not shop or not code or not state:
        raise HTTPException(
            status_code=400, detail="Missing required parameters: shop, code, or state"
        )

    expected_shop = _state_store.get(state)
    if expected_shop != shop:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    client_id, client_secret = _get_client_credentials()

    # Verify HMAC for security
    if not _verify_hmac(params, client_secret):
        raise HTTPException(status_code=400, detail="HMAC verification failed")

    # Exchange code for access token
    token_url = f"https://{shop}/admin/oauth/access_token"
    payload = {"client_id": client_id, "client_secret": client_secret, "code": code}
    async with httpx.AsyncClient() as c:
        resp = await c.post(token_url, json=payload, timeout=15.0)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Token exchange failed",
                "detail": str(exc),
                "response_text": resp.text,
            },
        )

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail={"message": "No access_token in response", "response": data},
        )

    # Persist token in the simple JSON token store
    token_store.save_token(shop, access_token)

    # Clean up state
    try:
        del _state_store[state]
    except KeyError:
        pass

    return {
        "shop": shop,
        "access_token": access_token,
        "note": "Token saved to token store. Use ShopifyClient(shop=...) to load it.",
    }
