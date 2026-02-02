from fastapi import APIRouter
# ✅ Import "play" ที่เป็น Folder (Package) แทนการ Import ไฟล์ย่อย
from app.api.v1.endpoints import auth, users, play, reward, shops, upload, system, media

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])

# ✅ บรรทัดเดียวจบ ครบทั้ง tickets, config, stats, risk
api_router.include_router(play.router, prefix="/play", tags=["play"])

api_router.include_router(reward.router, prefix="/reward", tags=["reward"])
api_router.include_router(shops.router, prefix="/shops", tags=["shops"])
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(media.router, prefix="/media", tags=["media"])