from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.shop import Shop  
from app.models.lotto import Ticket

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

# 1. ล้างข้อมูลทั้งระบบ (Global Cleanup)
@router.delete("/cleanup/global")
def cleanup_global_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Superadmin privilege required")

    try:
        # ลบตามลำดับ (ลูก -> แม่)
        db.execute(text("DELETE FROM ticket_items"))   
        db.execute(text("DELETE FROM tickets"))        
        db.execute(text("DELETE FROM lotto_results"))    
        
        db.commit()
        return {"status": "success", "message": "All operational data cleaned"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# 2. ล้างข้อมูลเฉพาะร้าน (Shop Cleanup)
@router.delete("/cleanup/shop/{shop_id}")
def cleanup_shop_data(
    shop_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Superadmin privilege required")

    try:
        params = {"sid": shop_id}
        
        # 1. ลบ Ticket Items
        db.execute(text("""
            DELETE FROM ticket_items 
            WHERE ticket_id IN (SELECT id FROM tickets WHERE shop_id = :sid)
        """), params)
        
        # 2. ลบ Tickets
        db.execute(text("DELETE FROM tickets WHERE shop_id = :sid"), params)

        db.commit()
        return {"status": "success", "message": f"Data for shop {shop_id} cleaned"}
    except Exception as e:
        db.rollback()
        # ✅ เติมบรรทัดนี้กลับเข้าไปครับ
        raise HTTPException(status_code=500, detail=str(e))

