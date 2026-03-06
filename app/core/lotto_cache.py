# app/core/lotto_cache.py
"""
Lotto Cache System - Optimized for Low Latency & High Consistency
จัดการ Cache รายการหวย พร้อม Thread-Safe และ Metrics
"""
from typing import List, Optional, Dict
import time
import threading
from app.schemas import LottoResponse

# ==================== Cache State ====================
_LOTTO_LIST_CACHE: Optional[List[Dict]] = None
_LAST_UPDATED: float = 0
_cache_lock = threading.Lock()  # ✅ Thread-safe protection

# ==================== Configuration ====================
CACHE_DURATION = 60  

# ==================== Metrics (สำหรับ Debug) ====================
_cache_hits = 0
_cache_misses = 0

def get_cached_lottos(db_fetch_callback) -> List[Dict]:
    """
    ดึงรายการหวยจาก Cache (Thread-Safe)
    
    Args:
        db_fetch_callback: ฟังก์ชันที่ query DB (ต้อง return List[LottoType])
    
    Returns:
        List[Dict]: รายการหวยทั้งหมด (เป็น Dict แทน ORM Objects)
    
    Note:
        - ใช้ threading.Lock ป้องกัน concurrent refresh
        - Cache duration = 1 วินาที (สมดุลระหว่างความเร็วและความแม่นยำ)
        - Auto-convert ORM → Dict เพื่อป้องกัน DetachedInstanceError
    """
    global _LOTTO_LIST_CACHE, _LAST_UPDATED, _cache_hits, _cache_misses
    
    current_time = time.time()
    
    # ✅ [FIX] ใช้ Lock ป้องกัน race condition จาก concurrent requests
    with _cache_lock:
        # เช็คว่าต้อง refresh cache หรือไม่
        need_refresh = (
            _LOTTO_LIST_CACHE is None or 
            (current_time - _LAST_UPDATED > CACHE_DURATION)
        )
        
        if need_refresh:
            _cache_misses += 1
            print(f"🔄 [Cache MISS] Refreshing Lotto Cache from DB (age: {current_time - _LAST_UPDATED:.2f}s)")
            
            try:
                # 1. Query Database
                start_time = time.time()
                lottos_orm = db_fetch_callback()
                query_time = (time.time() - start_time) * 1000  # ms
                
                # 2. Convert ORM → Pydantic → Dict
                # เพื่อตัดขาดจาก DB Session (ป้องกัน DetachedInstanceError)
                valid_lottos = []
                for lotto in lottos_orm:
                    try:
                        lotto_dict = LottoResponse.model_validate(lotto).model_dump()
                        valid_lottos.append(lotto_dict)
                    except Exception as conv_err:
                        print(f"⚠️ Failed to convert lotto {getattr(lotto, 'id', 'unknown')}: {conv_err}")
                        continue  # Skip invalid lotto
                
                # 3. Update cache
                _LOTTO_LIST_CACHE = valid_lottos
                _LAST_UPDATED = current_time
                
                print(f"✅ Cache refreshed: {len(valid_lottos)} lottos (query: {query_time:.0f}ms, hit rate: {_get_cache_hit_rate_unsafe():.1f}%)")
                
            except Exception as e:
                print(f"❌ Cache Refresh Error: {e}")
                # ถ้า refresh ไม่สำเร็จ และไม่มี cache เก่า → คืน []
                if _LOTTO_LIST_CACHE is None:
                    print("⚠️ No cache available, returning empty list")
                    return []
                else:
                    # ถ้ามี cache เก่า → ใช้ cache เก่าไปก่อน (stale is better than crash)
                    print(f"⚠️ Using stale cache (age: {current_time - _LAST_UPDATED:.1f}s)")
        else:
            # Cache Hit
            _cache_hits += 1
            cache_age = current_time - _LAST_UPDATED
            if _cache_hits % 100 == 0:  # Log ทุก 100 hits
                print(f"📊 Cache stats: {_cache_hits} hits, {_cache_misses} misses (hit rate: {_get_cache_hit_rate_unsafe():.1f}%)")
        
        return _LOTTO_LIST_CACHE if _LOTTO_LIST_CACHE else []

def invalidate_lotto_cache():
    """
    Force invalidate cache (เรียกเมื่อ Admin กดเพิ่ม/ลบ/แก้ไขหวย)
    
    Thread-Safe: ใช้ lock ป้องกัน concurrent invalidation
    """
    global _LOTTO_LIST_CACHE, _LAST_UPDATED
    
    with _cache_lock:
        _LOTTO_LIST_CACHE = None
        _LAST_UPDATED = 0  # ✅ Reset timestamp → บังคับให้ refresh ทันที
        print("🗑️ Invalidated Lotto Cache → next request will refresh")

def get_cache_stats() -> Dict:
    """
    ดึงสถิติ Cache สำหรับ Monitoring (Thread-Safe)
    """
    with _cache_lock:  # ✅ [FIX] ป้องกัน race condition
        return {
            "cache_hits": _cache_hits,
            "cache_misses": _cache_misses,
            "hit_rate": _get_cache_hit_rate_unsafe(),  # เรียก unsafe version (อยู่ใน lock แล้ว)
            "cached_items": len(_LOTTO_LIST_CACHE) if _LOTTO_LIST_CACHE else 0,
            "cache_age_seconds": time.time() - _LAST_UPDATED if _LAST_UPDATED > 0 else None,
            "cache_duration": CACHE_DURATION
        }

def get_cache_hit_rate() -> float:
    """
    คำนวณ Cache Hit Rate (%) - Thread-Safe
    
    Note: ใช้สำหรับเรียกจากภายนอก (เช่น logging)
    """
    with _cache_lock:  # ✅ [FIX] ป้องกัน race condition
        return _get_cache_hit_rate_unsafe()

def _get_cache_hit_rate_unsafe() -> float:
    """
    คำนวณ Cache Hit Rate (%) - Internal Use Only (ไม่มี lock)
    
    Warning: ต้องเรียกภายใน context ที่มี _cache_lock แล้วเท่านั้น!
    """
    total = _cache_hits + _cache_misses
    return (_cache_hits / total * 100) if total > 0 else 0.0

def reset_cache_metrics():
    """
    รีเซ็ต metrics (สำหรับ testing หรือ monitoring reset) - Thread-Safe
    """
    global _cache_hits, _cache_misses
    
    with _cache_lock:  # ✅ [FIX] ป้องกัน race condition
        _cache_hits = 0
        _cache_misses = 0
        print("🔄 Cache metrics reset")