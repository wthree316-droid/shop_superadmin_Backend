# backend/app/api/v1/router.py
from fastapi import APIRouter
from app.api.v1.endpoints import auth, users, play, reward, audit, shops, upload, system, topup

api_router = APIRouter()

# รวม endpoint ย่อยๆ เข้าด้วยกันที่นี่
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(play.router, prefix="/play", tags=["play"])
api_router.include_router(reward.router, prefix="/reward", tags=["reward"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
api_router.include_router(shops.router, prefix="/shops", tags=["shops"])
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(topup.router, prefix="/topup", tags=["topup"])