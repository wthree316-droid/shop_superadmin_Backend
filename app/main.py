from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.router import api_router

app = FastAPI(
    title="shop Multi-Tenant API",
    description="ระบบจัดการร้านค้าออนไลน์ระดับ Production",
    version="1.0.0"
)

origins = [
    "http://localhost:5173", # สำหรับพัฒนาในเครื่อง
    "http://127.0.0.1:5173",
    "https://shop-superadmin-system-jbany0v20-tanakrits-projects-7e4e9491.vercel.app", # โดเมน Vercel ของคุณ
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # ระบุโดเมนที่อนุญาต
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"] # อนุญาตให้เข้าถึง Header ทั้งหมด
)

app.include_router(api_router, prefix="/api/v1")


# 3. Health Check สำหรับ Cloud Run
@app.get("/")
def root():
    return {
        "status": "online",
        "message": "Welcome to shop API System",
        "version": "1.0.0"
    }

# หมายเหตุ: ไม่ต้องใส่ uvicorn.run ตรงนี้ 
# เพราะ Dockerfile ของเราใช้ Gunicorn รันจากภายนอกอยู่แล้ว