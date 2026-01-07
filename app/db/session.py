from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings # เดี๋ยวเราจะสร้าง config ใน step ถัดไป

# ถ้าใช้ Supabase แนะนำให้เติม query parameter นี้แก้ปัญหา connection pool
# แต่ถ้าใช้ string ปกติก็ใส่ได้เลย
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL

# สร้าง Engine
engine = create_engine(SQLALCHEMY_DATABASE_URL)

# สร้าง Session Factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Dependency Function (พระเอกของเรา)
# ฟังก์ชันนี้จะถูกเรียกทุกครั้งที่มี Request เข้ามา และปิด connection เมื่อเสร็จงาน
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()