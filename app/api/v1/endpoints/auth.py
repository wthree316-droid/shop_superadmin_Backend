from datetime import timedelta
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas import Token
from app.core.audit_logger import write_audit_log

router = APIRouter()

@router.post("/login", response_model=Token)
def login_access_token(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    # 1. ค้นหา User
    user = db.query(User).filter(User.username == form_data.username).first()
    
    # 2. ตรวจสอบรหัสผ่าน
    if not user or not security.verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
        
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    # 3. สร้าง Access Token (ปรับปรุงการเรียกใช้ตาม Canvas ใหม่)
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    background_tasks.add_task(
        write_audit_log,
        user=user,
        action="LOGIN",
        details={"username": user.username},
        request=request
    )

    # เรียกใช้ create_access_token โดยส่ง subject และ role ตาม signature ใหม่
    token = security.create_access_token(
        subject=user.id, 
        role=user.role.value, 
        expires_delta=access_token_expires
    )

    return {
        "access_token": token,
        "token_type": "bearer",
    }