from typing import Dict, List
import time
from datetime import date # <--- เพิ่ม import

# Structure: { "lotto_id": { "data": {...}, "date": date_obj, "timestamp": float } }
_RISK_CACHE: Dict[str, Dict] = {} 

def get_cached_risks(lotto_id: str, db_fetch_callback) -> Dict[str, str]:
    """
    ดึงเลขอั้นจาก Cache ถ้าไม่มี หรือเก่าเกินไป หรือข้ามวันแล้ว ให้ดึงใหม่จาก DB
    """
    current_time = time.time()
    today = date.today() # <--- วันที่ปัจจุบัน

    # เช็คเงื่อนไข:
    # 1. ไม่มี Cache
    # 2. วันที่ใน Cache ไม่ใช่วันนี้ (ข้ามวันแล้ว)
    # 3. Cache เก่าเกิน 5 นาที (300 วินาที)
    
    cache_entry = _RISK_CACHE.get(lotto_id)
    
    should_refresh = (
        cache_entry is None or 
        cache_entry.get("date") != today or
        (current_time - cache_entry.get("timestamp", 0) > 300)
    )

    if should_refresh:
        # ดึงจาก DB (Callback function)
        print(f"🔄 Refreshing Risk Cache for {lotto_id} (Date: {today})")
        risks_from_db = db_fetch_callback(lotto_id)
        
        # กรองเอาเฉพาะของวันนี้ด้วย (Double Check ในระดับ Cache Logic)
        # เผื่อ db_fetch_callback ส่งมาหมด
        today_risks = []
        for r in risks_from_db:
             # เช็คว่า created_at ตรงกับวันนี้ไหม
             if r.created_at.date() == today:
                 today_risks.append(r)

        # แปลงเป็น Dict
        risk_map = {r.number: r.risk_type for r in today_risks}
        
        _RISK_CACHE[lotto_id] = {
            "data": risk_map,
            "date": today,       # เก็บวันที่ของข้อมูลชุดนี้
            "timestamp": current_time
        }
        
    return _RISK_CACHE[lotto_id]["data"]

def invalidate_cache(lotto_id: str):
    """
    สั่งล้าง Cache
    """
    if lotto_id in _RISK_CACHE:
        del _RISK_CACHE[lotto_id]
        print(f"🗑️ Invalidated Risk Cache for {lotto_id}")