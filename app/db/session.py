# app/db/session.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    # 1. เช็ค Connection ก่อนใช้เสมอ
    pool_pre_ping=True, 
    
    # 🚀 2. Connection Pool Size (ขยายถนนหลัก: รับพร้อมกันได้ 20 คิวแบบไม่ต้องรอ)
    pool_size=20,
    
    # 🚀 3. Max Overflow (ขยายถนนรอง: ถ้า 20 คิวเต็ม อนุญาตให้เบียดเพิ่มได้อีก 40 คิว)
    # รวมแล้วระบบจะรับการประมวลผลฐานข้อมูลได้ 60 คิวพร้อมกัน!
    max_overflow=40,
    
    # 🚀 4. กำหนดเวลารอคิวสูงสุด 30 วินาที (ถ้าเกินให้ตัด Error ทันที ดีกว่าปล่อยให้เว็บค้าง)
    pool_timeout=30,
    
    # ใช้เป็น 1800 (30 นาที) เพื่อความชัวร์
    pool_recycle=1800, 

    # บังคับให้ส่งสัญญาณชีพ (Heartbeat) ไปหา Database เรื่อยๆ
    # ป้องกันไม่ให้ Firewall หรือ Supabase ตัดสายเมื่อไม่มีการใช้งาน
    connect_args={
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5
    }
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()