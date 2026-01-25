# app/core/risk_cache.py

from typing import Dict, List
import time
from datetime import datetime, timedelta 
from app.core.config import get_thai_now 

_RISK_CACHE: Dict[str, Dict] = {} 

def get_cached_risks(lotto_id: str, db_fetch_callback) -> Dict[str, str]:
    current_time = time.time()
    
    today = get_thai_now().date()

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
        # print(f"🔄 Refreshing Risk Cache for {lotto_id} (Date: {today})")
        risks_from_db = db_fetch_callback(lotto_id)
        
        # กรองเอาเฉพาะของวันนี้ด้วย
        today_risks = []
        for r in risks_from_db:
             # เช็คว่า created_at ตรงกับวันนี้ไหม
             if r.created_at.date() == today:
                 today_risks.append(r)

        # แปลงเป็น Dict { "เลข": "สถานะ" }
        risk_map = {}
        for r in today_risks:
            # ใช้ Key เป็น "เลข:ประเภท" เพื่อให้แยกกันชัดเจน
            bet_type_key = r.specific_bet_type if r.specific_bet_type else "ALL"
            key = f"{r.number}:{bet_type_key}"
            
            risk_map[key] = r.risk_type
        
        _RISK_CACHE[lotto_id] = {
            "data": risk_map,
            "date": today,       # เก็บวันที่ของข้อมูลชุดนี้
            "timestamp": current_time
        }
        
    return _RISK_CACHE[lotto_id]["data"]

def invalidate_cache(lotto_id: str):
    """
    สั่งล้าง Cache (เรียกเมื่อมีการเพิ่ม/ลบเลขอั้น)
    """
    if lotto_id in _RISK_CACHE:
        del _RISK_CACHE[lotto_id]
        print(f"🗑️ Invalidated Risk Cache for {lotto_id}")