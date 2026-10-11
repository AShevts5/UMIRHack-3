from fastapi import APIRouter

from app.api import analyze, doc_types, files, health, history, regulation

router = APIRouter()

router.include_router(health.router)
router.include_router(analyze.router)
router.include_router(doc_types.router)
router.include_router(regulation.router)
router.include_router(history.router)
router.include_router(files.router)