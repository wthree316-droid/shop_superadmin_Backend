# app/core/stats_cache.py
import time
import threading
from typing import Any, Dict, Callable

# เก็บข้อมูล Cache ในหน่วยความจำ (RAM)
_STATS_CACHE: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()

# ⏱️ ระยะเวลาที่ให้อยู่ใน Cache (60 วินาที)
# สถิติไม่จำเป็นต้อง Real-time ทุกวินาที ช้าไป 1 นาทีไม่มีผลเสียอะไรเลย
CACHE_TTL = 60  

def get_or_set_stats_cache(cache_key: str, fetch_func: Callable[[], Any], ttl: int = CACHE_TTL) -> Any:
    """
    ฟังก์ชันอเนกประสงค์สำหรับดึง Cache สถิติ 
    ถ้ายกเลิก/หมดอายุ จะเรียก `fetch_func()` เพื่อดึงจาก DB ใหม่
    """
    current_time = time.time()

    # 1. เช็คว่ามีของใน Cache ไหม (ใช้ Lock ป้องกันการอ่าน/เขียนพร้อมกัน)
    with _cache_lock:
        cache_entry = _STATS_CACHE.get(cache_key)
        
        # ถ้ามี Cache และยังไม่หมดอายุ -> ส่งของเก่ากลับไปเลย (ไวระดับ 0.001 วิ)
        if cache_entry and (current_time - cache_entry['timestamp'] < ttl):
            # print(f"⚡ [Cache HIT] {cache_key}")
            return cache_entry['data']

    # 2. ถ้าไม่มี Cache หรือหมดอายุ -> สั่งประมวลผลใหม่ผ่านฟังก์ชันที่ส่งมา
    # print(f"🔄 [Cache MISS] Querying DB for {cache_key}")
    data = fetch_func()

    # 3. เซฟของใหม่ลง Cache
    with _cache_lock:
        _STATS_CACHE[cache_key] = {
            'data': data,
            'timestamp': time.time()
        }

    return data

def invalidate_stats_cache(shop_id: str = None):
    """
    ฟังก์ชันสำหรับล้าง Cache 
    (เอาไว้เรียกตอนมีคนกดยกเลิกโพย เพื่อให้สถิติอัปเดตทันที)
    """
    with _cache_lock:
        if shop_id:
            # ล้างเฉพาะของร้านนั้นๆ
            keys_to_delete = [k for k in _STATS_CACHE.keys() if f"shop_{shop_id}" in k]
            for k in keys_to_delete:
                del _STATS_CACHE[k]
        else:
            # ล้างทั้งหมด
            _STATS_CACHE.clear()