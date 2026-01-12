from typing import List, Optional
import time

# เก็บรายชื่อหวยทั้งหมด (List of Dictionaries/Schemas)
_LOTTO_LIST_CACHE: Optional[List[dict]] = None
_LAST_UPDATED: float = 0
CACHE_DURATION = 300  # 5 นาที (เผื่อระบบ Auto Refresh ไม่ทำงาน อย่างน้อย 5 นาทีก็อัปเดตเอง)

def get_cached_lottos(db_fetch_callback) -> List[dict]:
    """
    ดึงรายการหวยจาก Cache
    db_fetch_callback: ฟังก์ชันที่ใช้ดึงข้อมูลจาก DB จริงๆ (ถ้า Cache ว่าง)
    """
    global _LOTTO_LIST_CACHE, _LAST_UPDATED
    current_time = time.time()

    # ถ้าไม่มี Cache หรือ Cache เก่าเกิน 5 นาที
    if _LOTTO_LIST_CACHE is None or (current_time - _LAST_UPDATED > CACHE_DURATION):
        print("🔄 Refreshing Lotto Menu Cache from DB")
        
        # ดึงจาก DB
        lottos_from_db = db_fetch_callback()
        
        # แปลงข้อมูลจาก ORM Model เป็น Dict หรือ Pydantic Schema เพื่อเก็บใน Ram
        # (สมมติว่าใช้ Pydantic .model_dump() หรือแปลงมือ)
        _LOTTO_LIST_CACHE = lottos_from_db
        _LAST_UPDATED = current_time
        
    return _LOTTO_LIST_CACHE

def invalidate_lotto_cache():
    """
    เรียกใช้เมื่อ Admin กด:
    1. เพิ่มหวยใหม่
    2. แก้ไขเวลา/รูปภาพ
    3. เปลี่ยนสถานะ Active/Inactive
    """
    global _LOTTO_LIST_CACHE
    _LOTTO_LIST_CACHE = None
    print("🗑️ Invalidated Lotto Cache")