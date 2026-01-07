from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.audit import AuditLog
from pydantic import BaseModel
from datetime import datetime
from uuid import UUID
from app.schemas import AuditLogResponse

router = APIRouter()


@router.get("/", response_model=List[AuditLogResponse])
def read_audit_logs(
    skip: int = 0,
    limit: int = 50,
    action: Optional[str] = None, # Filter ตามประเภท
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security: เฉพาะ superAdmin เท่านั้น
    if current_user.role != UserRole.superadmin: 
        raise HTTPException(status_code=403, detail="Superadmin privilege required")

    query = db.query(AuditLog)

    # Filter 1: Tenant Isolation (สำคัญมาก ห้ามลืม!)
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