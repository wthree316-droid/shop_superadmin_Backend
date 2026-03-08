from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.router import api_router

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.limiter import limiter

app = FastAPI(
    title="shop Multi-Tenant API",
    description="ระบบจัดการร้านค้าออนไลน์ระดับ Production",
    version="1.0.0"
)
app.state.limiter = limiter
# 🌟 3. บอกแอพว่า ถ้าใครยิงเกินโควตา ให้ตอบกลับด้วย Error ของ slowapi
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(.*\.ntlot\.com|ntlot\.com|localhost:\d+|127\.0\.0\.1:\d+)",
    
    allow_credentials=True, # สำคัญมาก! ต้องเป็น True ถึงจะส่ง Token/Cookie ข้ามโดเมนได้
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

app.include_router(api_router, prefix="/api/v1")


# 3. Health Check สำหรับ Cloud Run
@app.get("/")
def root():
    return {
        "status": "online",
        "message": "Welcome to shop API System",
        "version": "1.0.1"
    }

# หมายเหตุ: ไม่ต้องใส่ uvicorn.run ตรงนี้ 
# เพราะ Dockerfile ของเราใช้ Gunicorn รันจากภายนอกอยู่แล้ว