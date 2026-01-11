from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload # [เพิ่ม] joinedload
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
    action: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]: 
        raise HTTPException(status_code=403, detail="Not authorized")

    # [เพิ่ม] joinedload เพื่อดึงข้อมูล User และ Shop มาด้วยในคำสั่งเดียว
    query = db.query(AuditLog).options(
        joinedload(AuditLog.user).joinedload(User.shop)
    )

    # Filter 1: Tenant Isolation
    if current_user.shop_id:
        query = query.filter(AuditLog.shop_id == current_user.shop_id)

    # Filter 2: Action type
    if action:
        query = query.filter(AuditLog.action == action)

    logs = query.order_by(AuditLog.created_at.desc())\
                .offset(skip)\
                .limit(limit)\
                .all()
    
    # [สำคัญ] Map ข้อมูลเพื่อให้ Frontend แสดงชื่อได้
    results = []
    for log in logs:
        # แปลง SQLAlchemy Model เป็น Pydantic Model
        log_dict = AuditLogResponse.from_orm(log)
        
        # ยัดข้อมูลชื่อลงไป (Frontend จะได้ไม่ต้องยิง API ถามอีกรอบ)
        if log.user:
            log_dict.username = log.user.username
            if log.user.shop:
                log_dict.shop_name = log.user.shop.name
        
        results.append(log_dict)
                
    return results