import os
from pydantic_settings import BaseSettings
from datetime import datetime
import pytz # แนะนำให้ลง pip install pytz ถ้ายังไม่มี
class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    SUPABASE_URL: str
    SUPABASE_KEY: str

    class Config:
        env_file = ".env"

settings = Settings()

def get_thai_now():
    """ดึงเวลาปัจจุบันโซนไทย (Asia/Bangkok) เสมอ ไม่ว่า Server จะอยู่ที่ไหน"""
    tz = pytz.timezone('Asia/Bangkok')
    return datetime.now(tz)