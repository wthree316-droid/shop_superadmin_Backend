import os
from pydantic_settings import BaseSettings
from datetime import datetime, time, timedelta, date
import pytz # แนะนำให้ลง pip install pytz ถ้ายังไม่มี

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    SUPABASE_URL: str
    SUPABASE_KEY: str
    
    # เวลาตัดรอบวันใหม่ (Default 05:20 น.)
    DAY_CUTOFF_TIME: str = "05:20:00"

    class Config:
        env_file = ".env"

settings = Settings()

def get_thai_now():
    """ดึงเวลาปัจจุบันโซนไทย (Asia/Bangkok) เสมอ ไม่ว่า Server จะอยู่ที่ไหน"""
    tz = pytz.timezone('Asia/Bangkok')
    return datetime.now(tz)

def get_round_date(now_thai: datetime, cutoff_time_str: str = None) -> date:
    """
    คำนวณวันที่งวด (round_date) โดยพิจารณาจากเวลาตัดรอบ
    
    ถ้าเวลาปัจจุบัน < เวลาตัดรอบ → ให้ถือว่าเป็นงวดของเมื่อวาน
    ถ้าเวลาปัจจุบัน >= เวลาตัดรอบ → ให้ถือว่าเป็นงวดของวันนี้
    
    Args:
        now_thai: เวลาปัจจุบันในโซนไทย
        cutoff_time_str: เวลาตัดรอบ (เช่น "05:20:00") ถ้าไม่ระบุจะใช้ default
    
    Returns:
        วันที่งวด (date object)
    
    ตัวอย่าง:
        - เวลา 04:00 น. + cutoff 05:20 → round_date = เมื่อวาน
        - เวลา 05:30 น. + cutoff 05:20 → round_date = วันนี้
    """
    if cutoff_time_str is None:
        cutoff_time_str = settings.DAY_CUTOFF_TIME
    
    try:
        cutoff_time = datetime.strptime(cutoff_time_str, "%H:%M:%S").time()
    except:
        # ถ้า parse ไม่ได้ ใช้ default
        cutoff_time = time(5, 20, 0)
    
    current_time = now_thai.time()
    
    if current_time < cutoff_time:
        # ยังไม่ถึงเวลาตัด ให้ถือว่าเป็นงวดเมื่อวาน
        return (now_thai.date() - timedelta(days=1))
    else:
        # ถึงหรือเลยเวลาตัดแล้ว ให้ถือว่าเป็นงวดวันนี้
        return now_thai.date()