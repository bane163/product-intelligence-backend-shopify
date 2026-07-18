"""Agents router aggregator."""

from fastapi import APIRouter

from . import billing, compliance, drafts, files, imports, intelligence, llm_configs, runs, submit, submitted, wopi

router = APIRouter(prefix="/agents", tags=["agents"])

# Preserve the original endpoint paths by including each sub-router intact.
router.include_router(billing.router)
router.include_router(compliance.router)
router.include_router(files.router)
router.include_router(imports.router)
router.include_router(drafts.router)
router.include_router(intelligence.router)
router.include_router(submit.router)
router.include_router(submitted.router)
router.include_router(runs.router)
router.include_router(llm_configs.router)
router.include_router(wopi.router)
