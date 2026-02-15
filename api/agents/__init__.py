"""Agents router aggregator."""

from fastapi import APIRouter

from . import drafts, files, runs, submit, submitted, wopi

router = APIRouter(prefix="/agents", tags=["agents"])

# Preserve the original endpoint paths by including each sub-router intact.
router.include_router(files.router)
router.include_router(drafts.router)
router.include_router(submit.router)
router.include_router(submitted.router)
router.include_router(runs.router)
router.include_router(wopi.router)
