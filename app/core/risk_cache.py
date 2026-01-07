from typing import Dict, List
import time

# ตัวแปร Global เก็บ Cache
# Structure: { "lotto_id_uuid": { "12": "CLOSE", "59": "HALF" } }
_RISK_CACHE: Dict[str, Dict[str, str]] = {}
_LAST_UPDATED: Dict[str, float] = {}

def get_cached_risks(lotto_id: str, db_fetch_callback) -> Dict[str, str]:
    """
    ดึงเลขอั้นจาก Cache ถ้าไม่มี หรือเก่าเกินไป ให้ดึงใหม่จาก DB
    """
    current_time = time.time()
    
    # ถ้ายังไม่มีใน Cache หรือข้อมูลเก่าเกิน 5 นาที (กันพลาด)
    if lotto_id not in _RISK_CACHE or (current_time - _LAST_UPDATED.get(lotto_id, 0) > 300):
        # ดึงจาก DB (Callback function)
        print(f"🔄 Refreshing Risk Cache for {lotto_id}")
        risks_from_db = db_fetch_callback(lotto_id)
        
        # แปลงเป็น Dict เพื่อความเร็วในการค้นหา O(1)
        risk_map = {r.number: r.risk_type for r in risks_from_db}
        
        _RISK_CACHE[lotto_id] = risk_map
        _LAST_UPDATED[lotto_id] = current_time
        
    return _RISK_CACHE[lotto_id]

def invalidate_cache(lotto_id: str):
    """
    สั่งล้าง Cache เมื่อมีการเพิ่ม/ลบเลขอั้น (Admin กดปุ่ม)
    เพื่อให้ครั้งต่อไปดึงข้อมูลใหม่ทันที
    """
    if lotto_id in _RISK_CACHE:
        del _RISK_CACHE[lotto_id]
        print(f"🗑️ Invalidated Risk Cache for {lotto_id}")