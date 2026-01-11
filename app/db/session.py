# app/db/session.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL

#  เพิ่ม Argument สำหรับจัดการ Connection Pool
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    # 1. pool_pre_ping=True: เช็คก่อนเสมอว่า Connection ยังดีอยู่ไหม ถ้าตายจะต่อใหม่ให้เอง (สำคัญมาก!)
    pool_pre_ping=True, 
    
    # 2. pool_size: จำนวน Connection ที่เปิดค้างไว้ (Cloud Run ปกติใช้ 5-10 ก็พอต่อ 1 instance)
    pool_size=10, 
    
    # 3. max_overflow: ยอมให้เกิน pool_size ได้กี่อันช่วงคนเยอะ
    max_overflow=20,
    
    # 4. pool_recycle: รีไซเคิล connection ทุกๆ 1 ชั่วโมง (3600 วิ) ป้องกัน DB ตัดเพราะนานเกิน
    pool_recycle=3600
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()