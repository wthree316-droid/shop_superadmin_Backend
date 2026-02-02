from fastapi import APIRouter
from . import config, tickets, stats, risk 

router = APIRouter()

router.include_router(config.router)
router.include_router(tickets.router)
router.include_router(stats.router)
router.include_router(risk.router)