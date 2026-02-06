from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.shop import Shop  
from app.models.lotto import Ticket
from app.core import lotto_cache  # ✅ Import cache module

router = APIRouter()

@router.get("/stats")
def get_system_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")

    total_shops = db.query(Shop).count()
    active_shops = db.query(Shop).filter(Shop.is_active == True).count()
    total_users = db.query(User).count()
    total_tickets = db.query(Ticket).count()

    return {
        "total_shops": total_shops,
        "active_shops": active_shops,
        "total_users": total_users,
        "total_tickets": total_tickets
    }

@router.get("/cache/stats")
def get_cache_stats(
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    ดึงสถิติ Cache สำหรับ Monitoring (Admin/SuperAdmin เท่านั้น)
    """
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    return lotto_cache.get_cache_stats()

@router.post("/cache/invalidate")
def force_invalidate_cache(
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    Force invalidate cache ทันที (SuperAdmin เท่านั้น)
    """
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="SuperAdmin only")
    
    lotto_cache.invalidate_lotto_cache()
    return {"status": "success", "message": "Cache invalidated"}

@router.post("/cache/reset-metrics")
def reset_cache_metrics(
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    รีเซ็ต Cache Metrics (SuperAdmin เท่านั้น)
    """
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="SuperAdmin only")
    
    lotto_cache.reset_cache_metrics()
    return {"status": "success", "message": "Metrics reset"}

# 1. ล้างข้อมูลทั้งระบบ (Global Cleanup)
@router.delete("/cleanup/global")
def cleanup_global_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    ล้างข้อมูลธุรกรรมทั้งหมดในระบบ (SuperAdmin เท่านั้น)
    
    ลบ:
    - โพย (tickets)
    - ตัวเลขในโพย (ticket_items)
    - ผลรางวัล (lotto_results)
    - เลขอั้น (number_risks)
    
    เก็บไว้:
    - ร้านค้า (shops)
    - ผู้ใช้ (users)
    - หวย (lotto_types)
    - หมวดหมู่หวย (rate_profiles)
    """
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Superadmin privilege required")

    try:
        # ✅ ลบตามลำดับ (ลูก -> แม่) เพื่อหลีกเลี่ยง Foreign Key Constraint
        print("🧹 Starting Global Cleanup...")
        
        # 1. ลบ Ticket Items (ลูกของ Tickets)
        result = db.execute(text("DELETE FROM ticket_items"))
        print(f"   ✅ Deleted {result.rowcount} ticket_items")
        
        # 2. ลบ Tickets
        result = db.execute(text("DELETE FROM tickets"))
        print(f"   ✅ Deleted {result.rowcount} tickets")
        
        # 3. ลบผลรางวัล
        result = db.execute(text("DELETE FROM lotto_results"))
        print(f"   ✅ Deleted {result.rowcount} lotto_results")
        
        # 4. ✅ [NEW] ลบเลขอั้น
        result = db.execute(text("DELETE FROM number_risks"))
        print(f"   ✅ Deleted {result.rowcount} number_risks")
        
        db.commit()
        print("✅ Global Cleanup Complete!")
        
        return {
            "status": "success", 
            "message": "ล้างข้อมูลทั้งหมดเรียบร้อย (โพย, ตัวเลข, ผลรางวัล, เลขอั้น)"
        }
    except Exception as e:
        db.rollback()
        print(f"❌ Global Cleanup Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 2. ล้างข้อมูลเฉพาะร้าน (Shop Cleanup)
@router.delete("/cleanup/shop/{shop_id}")
def cleanup_shop_data(
    shop_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    ล้างข้อมูลธุรกรรมของร้านค้าเฉพาะ (SuperAdmin เท่านั้น)
    
    ลบ:
    - โพย (tickets)
    - ตัวเลขในโพย (ticket_items)
    - ผลรางวัล (lotto_results) ของหวยในร้านนี้
    - เลขอั้น (number_risks)
    
    เก็บไว้:
    - ร้านค้า (shop)
    - ผู้ใช้ (users)
    - หวย (lotto_types)
    - หมวดหมู่หวย (rate_profiles)
    """
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Superadmin privilege required")

    try:
        params = {"sid": shop_id}
        print(f"🧹 Starting Shop Cleanup for shop_id: {shop_id}")
        
        # 1. ลบ Ticket Items (ลูกของ Tickets)
        result = db.execute(text("""
            DELETE FROM ticket_items 
            WHERE ticket_id IN (SELECT id FROM tickets WHERE shop_id = :sid)
        """), params)
        print(f"   ✅ Deleted {result.rowcount} ticket_items")
        
        # 2. ลบ Tickets
        result = db.execute(text("DELETE FROM tickets WHERE shop_id = :sid"), params)
        print(f"   ✅ Deleted {result.rowcount} tickets")
        
        # 3. ✅ [NEW] ลบผลรางวัลของหวยในร้านนี้
        result = db.execute(text("""
            DELETE FROM lotto_results 
            WHERE lotto_id IN (SELECT id FROM lotto_types WHERE shop_id = :sid)
        """), params)
        print(f"   ✅ Deleted {result.rowcount} lotto_results")
        
        # 4. ✅ [FIX] ลบเลขอั้นผ่าน lotto_type_id (เพราะ shop_id nullable)
        result = db.execute(text("""
            DELETE FROM number_risks 
            WHERE lotto_type_id IN (SELECT id FROM lotto_types WHERE shop_id = :sid)
        """), params)
        print(f"   ✅ Deleted {result.rowcount} number_risks")

        db.commit()
        print(f"✅ Shop Cleanup Complete for shop_id: {shop_id}")
        
        return {
            "status": "success", 
            "message": f"ล้างข้อมูลร้าน {shop_id} เรียบร้อย (โพย, ตัวเลข, ผลรางวัล, เลขอั้น)"
        }
    except Exception as e:
        db.rollback()
        print(f"❌ Shop Cleanup Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

