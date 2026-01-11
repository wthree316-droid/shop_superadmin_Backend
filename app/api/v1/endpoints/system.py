from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole

router = APIRouter()

# 1. ล้างข้อมูลทั้งระบบ (Global Cleanup)
@router.delete("/cleanup/global")
def cleanup_global_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security ขั้นสูงสุด: ต้องเป็น Superadmin เท่านั้น
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Superadmin privilege required")

    try:
        # ลบตามลำดับ (ลูก -> แม่) เพื่อไม่ให้ติด Foreign Key
        db.execute(text("DELETE FROM ticket_items"))   # 1. รายการย่อย
        db.execute(text("DELETE FROM tickets"))        # 2. หัวบิล
        db.execute(text("DELETE FROM audit_logs"))     # 3. ประวัติ
        db.execute(text("DELETE FROM lotto_results"))  # 4. ผลรางวัล
        
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
        # [แก้ไข - สำคัญมาก] ใช้ Parameter Binding (:sid) แทนการต่อ String
        # เพื่อป้องกัน SQL Injection แม้ shop_id จะดูเหมือน UUID ก็ตาม
        
        # 1. ลบ Ticket Items (ใช้ Subquery)
        db.execute(text("""
            DELETE FROM ticket_items 
            WHERE ticket_id IN (SELECT id FROM tickets WHERE shop_id = :sid)
        """), {"sid": shop_id})
        
        # 2. ลบ Tickets ของร้านนี้
        db.execute(text("DELETE FROM tickets WHERE shop_id = :sid"), {"sid": shop_id})
        
        # 3. ลบ Logs ของร้านนี้
        db.execute(text("DELETE FROM audit_logs WHERE shop_id = :sid"), {"sid": shop_id})

        db.commit()
        return {"status": "success", "message": f"Data for shop {shop_id} cleaned"}
    except Exception as e:
        db.rollback()
        print(f"Cleanup Error: {e}") # print error ให้เห็นใน log ด้วย
        raise HTTPException(status_code=500, detail="Failed to cleanup shop data")