# app/core/lotto_cache.py
from typing import List, Optional, Dict
import time
from app.schemas import LottoResponse # ต้อง import Schema มาเพื่อแปลงข้อมูล

# เก็บเป็น List of Dictionaries แทน ORM Objects
_LOTTO_LIST_CACHE: Optional[List[Dict]] = None
_LAST_UPDATED: float = 0
CACHE_DURATION = 10  # ✅ [FIX] ลดเหลือ 10 วินาที (เดิม 300 วินาที / 5 นาที)

def get_cached_lottos(db_fetch_callback) -> List[Dict]:
    """
    ดึงรายการหวยจาก Cache
    db_fetch_callback: ฟังก์ชัน lambda ที่ query DB (ต้อง return List[LottoType])
    """
    global _LOTTO_LIST_CACHE, _LAST_UPDATED
    current_time = time.time()

    # ถ้าไม่มี Cache หรือ Cache เก่าเกิน 5 นาที
    if _LOTTO_LIST_CACHE is None or (current_time - _LAST_UPDATED > CACHE_DURATION):
        print("🔄 Refreshing Lotto Menu Cache from DB")
        
        try:
            # 1. ดึงข้อมูลดิบจาก DB (เป็น SQLAlchemy Objects)
            lottos_orm = db_fetch_callback()
            
            # 2. ✅ จุดสำคัญ: แปลง ORM -> Pydantic Model -> Dict ทันที
            # เพื่อตัดขาดจาก DB Session ป้องกัน DetachedInstanceError
            valid_lottos = []
            for lotto in lottos_orm:
                # แปลงผ่าน Schema เพื่อจัดการเรื่อง datetime/uuid ให้อัตโนมัติ
                lotto_dict = LottoResponse.model_validate(lotto).model_dump()
                valid_lottos.append(lotto_dict)

            _LOTTO_LIST_CACHE = valid_lottos
            _LAST_UPDATED = current_time
            
        except Exception as e:
            print(f"⚠️ Cache Error: {e}")
            # ถ้าแปลงไม่ผ่าน ให้คืนค่าว่างไปก่อน ดีกว่าระบบล่ม
            if _LOTTO_LIST_CACHE is None:
                return []
        
    return _LOTTO_LIST_CACHE

def invalidate_lotto_cache():
    """
    เรียกใช้เมื่อ Admin กดเพิ่ม/ลบ/แก้ไขหวย
    """
    global _LOTTO_LIST_CACHE, _LAST_UPDATED
    _LOTTO_LIST_CACHE = None
    _LAST_UPDATED = 0  # ✅ [FIX] Reset timestamp เพื่อบังคับให้ refresh cache ทันที
    print("🗑️ Invalidated Lotto Cache (forced refresh next request)")