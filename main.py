from fastapi import FastAPI
from auth import router as auth_router

# Import routers from the routes package
from routes.shopify_products import router as shopify_router
from routes.maf import router as microsoft_router


# FastAPI app instance — uvicorn/fastapi looks for an `app` variable by default
app = FastAPI()

app.include_router(shopify_router)
app.include_router(auth_router)
app.include_router(microsoft_router)


def main():
    # Keep existing CLI behaviour
    print("Hello from shopify-supabase-backend!")


if __name__ == "__main__":
    main()
