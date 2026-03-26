# backend/init_tables.py

from app.db.session import engine
from app.db.base_class import Base
from app.models.lotto import NumberRisk # Import เพื่อให้ SQLAlchemy รู้จัก Model นี้

def init_db():
    print("Creating database tables...")
    # คำสั่งนี้จะสร้างตารางที่ยังไม่มีใน DB (ตารางเดิมจะไม่หาย)
    Base.metadata.create_all(bind=engine)
    print("✅ Tables created successfully!")

if __name__ == "__main__":
    init_db()