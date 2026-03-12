# app/core/stats_cache.py
import time
import threading
from typing import Any, Dict, Callable

_STATS_CACHE: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()

# 🌟 เพิ่ม Dict สำหรับเก็บกุญแจล็อคเฉพาะแต่ละ Cache Key ป้องกันคนแย่งกันดึง DB
_key_locks: Dict[str, threading.Lock] = {}

CACHE_TTL = 60  

def get_or_set_stats_cache(cache_key: str, fetch_func: Callable[[], Any], ttl: int = CACHE_TTL) -> Any:
    current_time = time.time()

    # 1. เช็คว่ามีของใน Cache ไหม
    with _cache_lock:
        cache_entry = _STATS_CACHE.get(cache_key)
        if cache_entry and (current_time - cache_entry['timestamp'] < ttl):
            return cache_entry['data']
            
        # ถ้าไม่มีล็อคของ Key นี้ ให้สร้างขึ้นมา
        if cache_key not in _key_locks:
            _key_locks[cache_key] = threading.Lock()
        key_lock = _key_locks[cache_key]

    # 2. 🌟 บังคับให้เข้าคิว (แก้ปัญหา Cache Stampede ยิง DB พร้อมกันร้อยคน)
    with key_lock:
        # Double-check: พอเข้ามาในคิวได้แล้ว เช็คอีกรอบเผื่อว่าคนก่อนหน้าเพิ่งดึงข้อมูลเสร็จ
        with _cache_lock:
            cache_entry = _STATS_CACHE.get(cache_key)
            if cache_entry and (current_time - cache_entry['timestamp'] < ttl):
                return cache_entry['data']
        
        # 3. ให้คนแรกแค่ "คนเดียว" เท่านั้นที่เป็นคนวิ่งไปหา Database!
        data = fetch_func()

        # 4. เซฟของใหม่ลง Cache และแจกจ่ายให้คนที่รอในคิว
        with _cache_lock:
            _STATS_CACHE[cache_key] = {
                'data': data,
                'timestamp': time.time()
            }

    return data

def invalidate_stats_cache(shop_id: str = None):
    with _cache_lock:
        if shop_id:
            keys_to_delete = [k for k in _STATS_CACHE.keys() if f"shop_{shop_id}" in k]
            for k in keys_to_delete:
                del _STATS_CACHE[k]
        else:
            _STATS_CACHE.clear()