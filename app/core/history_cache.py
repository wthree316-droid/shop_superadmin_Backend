# app/core/history_cache.py
import time
import threading
from typing import Callable, Any

# โครงสร้าง: { "key_ที่ใช้ค้นหา": {"data": [...], "expire_at": 17000000} }
_HISTORY_CACHE = {}
_cache_lock = threading.Lock()

# จำกัดขนาด Cache ไม่ให้เกิน 2,000 รายการ ป้องกัน RAM เซิร์ฟเวอร์ระเบิด
MAX_CACHE_ITEMS = 200 

def get_or_set_history(cache_key: str, is_past: bool, fetch_func: Callable[[], Any]) -> Any:
    global _HISTORY_CACHE
    current_time = time.time()
    
    with _cache_lock:
        # 1. มีข้อมูลใน Cache และยังไม่หมดอายุไหม?
        if cache_key in _HISTORY_CACHE:
            cached_item = _HISTORY_CACHE[cache_key]
            if current_time < cached_item["expire_at"]:
                return cached_item["data"]
            else:
                del _HISTORY_CACHE[cache_key] # ลบทิ้งถ้าหมดอายุ

        # 2. ถ้าไม่มี หรือหมดอายุ -> ให้เรียก fetch_func() เพื่อดึง DB ใหม่
    
    # (ดึง DB นอก Lock เพื่อไม่ให้ Block คนอื่น)
    fresh_data = fetch_func()
    
    with _cache_lock:
        # 3. ล้างขยะถ้า RAM เริ่มตึง (ลบตัวที่หมดอายุทิ้งให้หมด)
        if len(_HISTORY_CACHE) >= MAX_CACHE_ITEMS:
            keys_to_delete = [k for k, v in _HISTORY_CACHE.items() if v["expire_at"] < current_time]
            for k in keys_to_delete:
                del _HISTORY_CACHE[k]
            
            # ถ้ายังเกินอยู่ (มีแต่ของสด) ให้ลบแบบสุ่มทิ้งไป 20% เพื่อคืนพื้นที่ RAM
            if len(_HISTORY_CACHE) >= MAX_CACHE_ITEMS:
                keys = list(_HISTORY_CACHE.keys())[:int(MAX_CACHE_ITEMS * 0.2)]
                for k in keys:
                    del _HISTORY_CACHE[k]

        # 4. 🌟 Smart TTL Logic: 
        # ถ้าเป็นข้อมูล "อดีตล้วนๆ" (is_past = True) -> จำยาว 24 ชั่วโมง (86400 วิ)
        # ถ้ามี "วันนี้" รวมอยู่ด้วย -> จำสั้นๆ แค่ 15 วินาที (เพื่อให้คนกดรัวๆ DB ไม่พัง)
        ttl_seconds = 86400 if is_past else 15
        
        _HISTORY_CACHE[cache_key] = {
            "data": fresh_data,
            "expire_at": current_time + ttl_seconds
        }
        
    return fresh_data

# เพิ่มฟังก์ชันนี้ไว้ล่างสุดของไฟล์ เพื่อใช้ล้างแคชตอนแอดมินกดแจกรางวัล
def clear_all_history_cache():
    global _HISTORY_CACHE
    with _cache_lock:
        _HISTORY_CACHE.clear()