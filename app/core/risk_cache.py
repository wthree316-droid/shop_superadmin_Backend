# app/core/risk_cache.py
from typing import Dict, List, Any
import time
import threading
from datetime import datetime, timedelta 
from app.core.config import get_thai_now 

_RISK_CACHE: Dict[str, Dict[str, Any]] = {} 
_cache_lock = threading.Lock()
_key_locks: Dict[str, threading.Lock] = {} # 🌟 เพิ่มระบบกุญแจ

def get_cached_risks(lotto_id: str, db_fetch_callback) -> Dict[str, str]:
    current_time = time.time()
    today = get_thai_now().date()

    with _cache_lock:
        cache_entry = _RISK_CACHE.get(lotto_id)
        # เช็คว่ามีของใหม่ และเป็นของวันนี้ไหม
        if cache_entry and cache_entry.get("date") == today and (current_time - cache_entry.get("timestamp", 0) <= 300):
            return cache_entry["data"]
            
        if lotto_id not in _key_locks:
            _key_locks[lotto_id] = threading.Lock()
        key_lock = _key_locks[lotto_id]

    # 🌟 บังคับให้เข้าคิว (แก้ปัญหาคนดึง DB พร้อมกัน)
    with key_lock:
        with _cache_lock:
            cache_entry = _RISK_CACHE.get(lotto_id)
            if cache_entry and cache_entry.get("date") == today and (current_time - cache_entry.get("timestamp", 0) <= 300):
                return cache_entry["data"]
        
        # ให้คนแรกคนเดียววิ่งไปดึง DB
        risks_from_db = db_fetch_callback(lotto_id)
        
        today_risks = [r for r in risks_from_db if r.created_at.date() == today]

        risk_map = {}
        for r in today_risks:
            bet_type_key = r.specific_bet_type if r.specific_bet_type else "ALL"
            key = f"{r.number}:{bet_type_key}"
            risk_map[key] = r.risk_type
        
        with _cache_lock:
            _RISK_CACHE[lotto_id] = {
                "data": risk_map,
                "date": today,
                "timestamp": current_time
            }
        
    return _RISK_CACHE[lotto_id]["data"]

def invalidate_cache(lotto_id: str):
    with _cache_lock:
        if lotto_id in _RISK_CACHE:
            del _RISK_CACHE[lotto_id]