from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.router import api_router

import os
import uvicorn

app = FastAPI(title="Lotto Multi-Tenant API")

# 1. ตั้งค่า CORS (สำคัญมาก ไม่งั้น Frontend ยิงไม่เข้า)
origins = [
    "http://localhost:5173",    # Vite default port
    "http://127.0.0.1:5173",
    "http://shop-superadmin-system.vercel.app",    # เผื่อไว้
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # อนุญาตทุก Method (GET, POST, PUT, DELETE)
    allow_headers=["*"],  # อนุญาตทุก Header
)

app.include_router(api_router, prefix="/api/v1")

@app.get("/")
def root():
    return {"message": "Welcome to Lotto API System"}

if __name__ == "__main__":
    # อ่านค่า PORT จาก Environment ถ้าไม่มีให้ใช้ 8000
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)