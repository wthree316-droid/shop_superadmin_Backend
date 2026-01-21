# app/core/risk_cache.py

from typing import Dict, List
import time
from datetime import datetime, timedelta # ✅ แก้ import

# Structure: { "lotto_id": { "data": {...}, "date": date_obj, "timestamp": float } }
_RISK_CACHE: Dict[str, Dict] = {} 

def get_cached_risks(lotto_id: str, db_fetch_callback) -> Dict[str, str]:
    """
    ดึงเลขอั้นจาก Cache ถ้าไม่มี หรือเก่าเกินไป หรือข้ามวันแล้ว ให้ดึงใหม่จาก DB
    """
    current_time = time.time()
    
    # ✅ แก้ไข: หาวันที่ปัจจุบันแบบ Timezone Thai (UTC+7)
    # เพื่อให้สอดคล้องกับ Logic ใน play.py
    today = (datetime.utcnow() + timedelta(hours=7)).date()

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
            # เก็บแบบแยกประเภทด้วย ถ้ามี
            # แต่ถ้าโครงสร้างเดิมเก็บแค่เลข ก็ใช้ r.number
            # (ตามโค้ดเดิมของคุณเก็บแค่ number เป็น key)
            risk_map[r.number] = r.risk_type
            
            # ⚠️ เสริม: ถ้าในอนาคตคุณแยกประเภท (เช่น 3ตัวบนปิด, 2ตัวล่างเปิด)
            # ต้องแก้ key ตรงนี้ให้ละเอียดขึ้น เช่น f"{r.number}:{r.specific_bet_type}"
        
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