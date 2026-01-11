from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.audit import AuditLog
from app.schemas import AuditLogResponse

router = APIRouter()

@router.get("/", response_model=List[AuditLogResponse])
def read_audit_logs(
    skip: int = 0,
    limit: int = 50,
    action: Optional[str] = None, # Filter ตามประเภท action
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # [แก้ไข 1] Security: อนุญาตทั้ง Superadmin และ Admin เจ้าของร้าน
    if current_user.role not in [UserRole.superadmin, UserRole.admin]: 
        raise HTTPException(status_code=403, detail="Not authorized")

    query = db.query(AuditLog)

    # [Logic เดิมดีแล้ว] Filter 1: Tenant Isolation
    # - ถ้าเป็น Admin: มี shop_id -> เห็นแค่ร้านตัวเอง
    # - ถ้าเป็น Superadmin: ไม่มี shop_id -> เห็นทั้งหมด (หรือเห็นร้านที่ตัวเองสังกัดถ้ามี)
    if current_user.shop_id:
        query = query.filter(AuditLog.shop_id == current_user.shop_id)

    # Filter 2: Action type
    if action:
        query = query.filter(AuditLog.action == action)

    # Order by ล่าสุดก่อน
    logs = query.order_by(AuditLog.created_at.desc())\
                .offset(skip)\
                .limit(limit)\
                .all()
                
    return logs