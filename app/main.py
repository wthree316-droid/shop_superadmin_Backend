from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.router import api_router

app = FastAPI(
    title="shop Multi-Tenant API",
    description="ระบบจัดการร้านค้าออนไลน์ระดับ Production",
    version="1.0.0"
)

# 1. การตั้งค่า CORS (Cross-Origin Resource Sharing)
# เพื่อความปลอดภัยและความง่ายในการใช้งานร่วมกับ Vercel
app.add_middleware(
    CORSMiddleware,
    # ในช่วงพัฒนาใช้ ["*"] ได้ แต่ถ้าจะเอาขึ้นจริงแนะนำให้ใส่ URL ของ Vercel ลงไปแทน "*"
    allow_origins=["*"], 
    allow_credentials=True, # สำคัญ: ต้องเป็น True เพื่อให้หน้าบ้านส่ง Token ผ่าน Header ได้
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# 2. รวม API Router
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