"""Wrapper router for agents routes.

This module preserves the original public `router` import so existing imports
(`from routes.agents import router as agents_router`) remain compatible.

It composes `files` and `wopi` routers under the `/agents` prefix.
"""

from fastapi import APIRouter

from .agents.files import router as files_router
from .agents.wopi import router as wopi_router

router = APIRouter(prefix="/agents", tags=["agents"])

# Include sub-routers for organization
router.include_router(files_router)
router.include_router(wopi_router)
