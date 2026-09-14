"""Aggregates every /v1 route module behind one router."""
from fastapi import APIRouter

from . import analyses, escalations, screen, system, tools

router = APIRouter(prefix="/v1")
router.include_router(system.router)
# Level 1 before Level 2, so the free tier is the first thing in /docs that a
# reader meets - it is the endpoint most callers should start from.
router.include_router(screen.router)
router.include_router(analyses.router)
router.include_router(escalations.router)
router.include_router(tools.router)
