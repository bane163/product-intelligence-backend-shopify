from fastapi import FastAPI, APIRouter, HTTPException
from models import ProductCreate, ProductUpdate
from shopify import ShopifyClient
from auth import router as auth_router


# FastAPI app instance — uvicorn/fastapi looks for an `app` variable by default
app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello from shopify-supabase-backend!"}


# --- Shopify product CRUD endpoints -------------------------------------------------
router = APIRouter(prefix="/shopify/products", tags=["shopify"])


# The ShopifyClient reads SHOPIFY_STORE and SHOPIFY_ACCESS_TOKEN from environment by default
client = ShopifyClient()


@router.post("/", summary="Create product")
async def create_product(payload: ProductCreate):
    resp = await client.create_product(payload.title, payload.body_html or "", payload.vendor)
    errors = resp.get("data", {}).get("productCreate", {}).get("userErrors")
    if errors:
        raise HTTPException(status_code=400, detail=resp)
    return resp.get("data", {}).get("productCreate", {})


@router.get("/{gid}", summary="Get product by gid")
async def get_product(gid: str):
    resp = await client.get_product(gid)
    node = resp.get("data", {}).get("node")
    if not node:
        raise HTTPException(status_code=404, detail=resp)
    return node


@router.put("/{gid}", summary="Update product")
async def update_product(gid: str, payload: ProductUpdate):
    resp = await client.update_product(gid, payload.title, payload.body_html)
    errors = resp.get("data", {}).get("productUpdate", {}).get("userErrors")
    if errors:
        raise HTTPException(status_code=400, detail=resp)
    return resp.get("data", {}).get("productUpdate", {})


@router.delete("/{gid}", summary="Delete product")
async def delete_product(gid: str):
    resp = await client.delete_product(gid)
    errors = resp.get("data", {}).get("productDelete", {}).get("userErrors")
    if errors:
        raise HTTPException(status_code=400, detail=resp)
    return resp.get("data", {}).get("productDelete", {})


app.include_router(router)
app.include_router(auth_router)


def main():
    # Keep existing CLI behaviour
    print("Hello from shopify-supabase-backend!")


if __name__ == "__main__":
    main()
