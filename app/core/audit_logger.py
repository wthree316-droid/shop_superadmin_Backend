# app/core/audit_logger.py (สร้างไฟล์นี้ทับของเดิม)

from sqlalchemy.orm import Session
from app.db.session import SessionLocal # เรียกตัวสร้าง Session มาใช้เอง
from app.models.audit import AuditLog
from app.models.user import User
from fastapi import Request
from typing import Optional, Any
import json

# Helper ดึง IP
def get_client_ip(request: Request) -> str:
    if not request: return "0.0.0.0"
    if request.headers.get("x-forwarded-for"):
        return request.headers.get("x-forwarded-for").split(",")[0]
    return request.client.host if request.client else "0.0.0.0"

def write_audit_log(
    user: User,
    action: str,
    target_table: Optional[str] = None,
    target_id: Optional[str] = None, # UUID หรือ Int ก็ได้ เก็บเป็น String
    details: Optional[dict] = None,
    request: Optional[Request] = None,
    # db: Session = None <-- ลบอันนี้ออก ไม่รับ Session จากข้างนอกแล้ว
):
    """
    ฟังก์ชันนี้จะสร้าง DB Session ของตัวเอง เพื่อป้องกันปัญหา Session Closed ใน Background Task
    """
    db = SessionLocal() # [1] เปิด Session ใหม่
    try:
        ip = get_client_ip(request) if request else "0.0.0.0"
        user_agent = request.headers.get("user-agent") if request else "system"

        log_entry = AuditLog(
            user_id=user.id,
            shop_id=user.shop_id,
            action=action,
            target_table=target_table,
            target_id=str(target_id) if target_id else None,
            details=details,
            ip_address=ip,
            user_agent=user_agent
        )
        
        db.add(log_entry)
        db.commit() # [2] บันทึก
        # print(f"✅ Audit Log Saved: {action}") # Debug ดูใน Terminal ได้

    except Exception as e:
        print(f"❌ Failed to write audit log: {e}")
        db.rollback()
    finally:
        db.close() # [3] ปิด Session เสมอ