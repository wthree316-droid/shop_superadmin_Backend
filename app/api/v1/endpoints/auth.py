from datetime import datetime, timedelta
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

# Config สำหรับระบบป้องกัน Brute Force
MAX_FAILED_ATTEMPTS = 5       # จำนวนครั้งที่ให้ผิดได้
LOCKOUT_DURATION_MINUTES = 15 # ระยะเวลาที่ล็อค (นาที)

@router.post("/login", response_model=Token)
def login_access_token(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    # 1. ค้นหา User
    user = db.query(User).filter(User.username == form_data.username).first()
    
    # ถ้าไม่เจอ User (Return 401 ทันที)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # 2. [Security] ตรวจสอบว่าบัญชีถูกล็อคอยู่ไหม?
    if user.locked_until:
        if user.locked_until > datetime.now():
            # ยังไม่ถึงเวลาปลดล็อค
            wait_time = user.locked_until - datetime.now()
            minutes = int(wait_time.total_seconds() / 60) + 1
            raise HTTPException(
                status_code=400, 
                detail=f"บัญชีถูกระงับชั่วคราวเนื่องจากใส่รหัสผิดเกินกำหนด กรุณาลองใหม่ในอีก {minutes} นาที"
            )
        else:
            # หมดเวลาล็อคแล้ว -> รีเซ็ตค่าอัตโนมัติ
            user.locked_until = None
            user.failed_attempts = 0
            db.add(user)
            db.commit()
            db.refresh(user)

    # 3. ตรวจสอบรหัสผ่าน
    if not security.verify_password(form_data.password, user.password_hash):
        # --- รหัสผิด ---
        user.failed_attempts = (user.failed_attempts or 0) + 1
        
        # ถ้าผิดครบโควตา -> ล็อคบัญชี
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = datetime.now() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
            db.add(user)
            db.commit()
            
            # (Optional) Log ว่าโดนล็อค
            background_tasks.add_task(
                write_audit_log,
                user=user,
                action="ACCOUNT_LOCKED",
                details={"reason": "Too many failed attempts"},
                request=request
            )
            
            raise HTTPException(
                status_code=400, 
                detail=f"คุณใส่รหัสผิดเกิน {MAX_FAILED_ATTEMPTS} ครั้ง บัญชีถูกระงับ {LOCKOUT_DURATION_MINUTES} นาที"
            )
        
        db.add(user)
        db.commit()
        
        remaining = MAX_FAILED_ATTEMPTS - user.failed_attempts
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"รหัสผ่านไม่ถูกต้อง (เหลือโอกาส {remaining} ครั้ง)"
        )
        
    # 4. ตรวจสอบสถานะ User / Shop
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    # เช็คสถานะร้านด้วย ถ้า user มีสังกัดร้าน
    if user.shop_id and user.shop: 
        if not user.shop.is_active:
            raise HTTPException(status_code=400, detail="Shop is suspended. Contact support.")

    # 5. [Success] รหัสถูก -> รีเซ็ตค่าความผิดพลาดเป็น 0 (ถ้ามีค้างอยู่)
    if user.failed_attempts > 0 or user.locked_until is not None:
        user.failed_attempts = 0
        user.locked_until = None
        db.add(user)
        db.commit()
        db.refresh(user)

    # 6. สร้าง Access Token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    background_tasks.add_task(
        write_audit_log,
        user=user,
        action="LOGIN",
        details={"username": user.username},
        request=request
    )

    token = security.create_access_token(
        subject=user.id, 
        role=user.role.value, 
        expires_delta=access_token_expires
    )

    return {
        "access_token": token,
        "token_type": "bearer",
    }