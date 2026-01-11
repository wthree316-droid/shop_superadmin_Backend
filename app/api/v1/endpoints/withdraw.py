# app/api/v1/endpoints/withdraw.py

from typing import List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime

from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.topup import WithdrawRequest # Import Model ที่เพิ่งสร้าง
from app.schemas import WithdrawCreate, WithdrawResponse, TopupAction
from app.core.audit_logger import write_audit_log

router = APIRouter()

# 1. แจ้งถอนเงิน (User)
@router.post("/requests", response_model=WithdrawResponse)
def create_withdraw_request(
    withdraw_in: WithdrawCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Lock User เพื่อเช็คยอดเงิน
    user = db.query(User).filter(User.id == current_user.id).with_for_update().first()
    
    if user.credit_balance < withdraw_in.amount:
        raise HTTPException(status_code=400, detail="ยอดเงินคงเหลือไม่พอ")

    # 1. ตัดเงินทันที (Pending)
    old_balance = user.credit_balance
    user.credit_balance -= withdraw_in.amount
    
    # 2. สร้างรายการถอน
    new_req = WithdrawRequest(
        shop_id=user.shop_id,
        user_id=user.id,
        **withdraw_in.dict(),
        status="PENDING"
    )
    db.add(new_req)
    
    # 3. Log การตัดเงิน
    background_tasks.add_task(
        write_audit_log,
        user=current_user,
        action="REQUEST_WITHDRAW",
        target_id=current_user.id,
        details={
            "amount": float(withdraw_in.amount),
            "old_balance": float(old_balance),
            "new_balance": float(user.credit_balance)
        },
        request=request
    )
    
    db.commit()
    db.refresh(new_req)
    return new_req

# 2. ดูประวัติการถอน (User)
@router.get("/requests", response_model=List[WithdrawResponse])
def get_withdraw_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    return db.query(WithdrawRequest).filter(
        WithdrawRequest.user_id == current_user.id
    ).order_by(WithdrawRequest.created_at.desc()).limit(50).all()

# 3. จัดการรายการถอน (Admin: อนุมัติ/ปฏิเสธ)
@router.put("/requests/{req_id}/action")
def process_withdraw_request(
    req_id: UUID,
    action_in: TopupAction, # Reuse Schema เดิมที่มี status/remark
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    withdraw_req = db.query(WithdrawRequest).filter(WithdrawRequest.id == req_id).with_for_update().first()
    if not withdraw_req or withdraw_req.status != "PENDING":
        raise HTTPException(status_code=400, detail="รายการนี้ถูกจัดการไปแล้ว")

    # ถ้าอนุมัติ -> ไม่ต้องทำอะไรกับเงิน (เพราะตัดไปแล้วตอนแจ้ง) แค่เปลี่ยนสถานะ
    if action_in.status == "APPROVED":
        withdraw_req.status = "APPROVED"
        withdraw_req.approved_by = current_user.id
        withdraw_req.approved_at = datetime.now()

    # ถ้าปฏิเสธ -> ต้องคืนเงินลูกค้า
    elif action_in.status == "REJECTED":
        user = db.query(User).filter(User.id == withdraw_req.user_id).with_for_update().first()
        user.credit_balance += withdraw_req.amount # คืนเงิน
        
        withdraw_req.status = "REJECTED"
        withdraw_req.admin_remark = action_in.remark
        withdraw_req.approved_by = current_user.id
        withdraw_req.approved_at = datetime.now()

        # Log การคืนเงิน
        background_tasks.add_task(
            write_audit_log,
            user=current_user,
            action="REJECT_WITHDRAW_REFUND",
            target_id=user.id,
            details={"refund_amount": float(withdraw_req.amount)},
            request=request
        )

    db.commit()
    return {"status": "success"}