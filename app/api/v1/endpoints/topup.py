from typing import List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session, joinedload
from uuid import UUID
from datetime import datetime

from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.core.audit_logger import write_audit_log
from app.core.notify import send_line_message
from app.models.shop import Shop 
# คุณต้องสร้าง Models ใน Python ให้ตรงกับ SQL ที่สร้างด้วยนะครับ
# สมมติว่าสร้าง class TopupRequest และ ShopBankAccount ใน models/topup.py แล้ว
from app.models.topup import TopupRequest, ShopBankAccount 
from app.schemas import (
    BankAccountCreate, BankAccountResponse,
    TopupCreate, TopupResponse, TopupAction,
    WithdrawCreate, WithdrawResponse
)

router = APIRouter()

# ------------------------------------
# ส่วนจัดการบัญชีธนาคาร (Admin ตั้งค่า)
# ------------------------------------
@router.post("/banks", response_model=BankAccountResponse)
def add_bank_account(
    bank_in: BankAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    new_bank = ShopBankAccount(
        shop_id=current_user.shop_id,
        **bank_in.dict()
    )
    db.add(new_bank)
    db.commit()
    db.refresh(new_bank)
    return new_bank

@router.get("/banks", response_model=List[BankAccountResponse])
def get_bank_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Member เห็นเพื่อโอนเงิน, Admin เห็นเพื่อจัดการ
    if not current_user.shop_id:
        return []
    return db.query(ShopBankAccount).filter(
        ShopBankAccount.shop_id == current_user.shop_id,
        ShopBankAccount.is_active == True
    ).all()


# ------------------------------------
# ส่วนแจ้งฝากเงิน (Member -> Admin)
# ------------------------------------

# 1. Member ส่งคำขอ
@router.post("/requests", response_model=TopupResponse)
def create_topup_request(
    topup_in: TopupCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    new_req = TopupRequest(
        shop_id=current_user.shop_id,
        user_id=current_user.id,
        amount=topup_in.amount,
        proof_image=topup_in.proof_image,
        status="PENDING"
    )
    db.add(new_req)
    db.commit()
    db.refresh(new_req)

    # --- [ส่วนแจ้งเตือน LINE แบบใหม่] ---
    shop = db.query(Shop).filter(Shop.id == current_user.shop_id).first()
    
    # เช็คว่ามีทั้ง Token และ Target ID ครบไหม
    if shop and shop.line_channel_token and shop.line_target_id:
        msg = f"💰 แจ้งฝากใหม่!\n" \
              f"User: {current_user.username}\n" \
              f"ยอดเงิน: {topup_in.amount:,.2f} บาท\n" \
              f"เวลา: {datetime.now().strftime('%H:%M:%S')}"
        
        background_tasks.add_task(
            send_line_message,
            channel_token=shop.line_channel_token,
            target_id=shop.line_target_id,
            message=msg,
            image_url=topup_in.proof_image
        )
    
    return new_req

# 2. ดูรายการคำขอ (Admin ดูทั้งหมด / Member ดูของตัวเอง)
@router.get("/requests", response_model=List[TopupResponse])
def get_topup_requests(
    status: str = "PENDING", # Filter สถานะได้
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    query = db.query(TopupRequest).options(joinedload(TopupRequest.user))
    
    if current_user.role == UserRole.member:
        # Member เห็นแค่ของตัวเอง
        query = query.filter(TopupRequest.user_id == current_user.id)
    elif current_user.role == UserRole.admin:
        # Admin เห็นของทั้งร้าน
        query = query.filter(TopupRequest.shop_id == current_user.shop_id)
    
    if status:
        query = query.filter(TopupRequest.status == status)
        
    results = query.order_by(TopupRequest.created_at.desc()).offset(skip).limit(limit).all()
    
    # Map username manual (หรือใช้ schema config)
    for r in results:
        r.username = r.user.username
        
    return results

# 3. Admin กดยืนยัน/ปฏิเสธ (หัวใจสำคัญ!)
@router.put("/requests/{req_id}/action")
def process_topup_request(
    req_id: UUID,
    action_in: TopupAction,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security Check
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Lock Row for update (ป้องกัน Admin กดซ้อนกัน 2 คน)
    topup_req = db.query(TopupRequest).filter(TopupRequest.id == req_id).with_for_update().first()
    
    if not topup_req:
        raise HTTPException(status_code=404, detail="Request not found")
        
    if topup_req.status != "PENDING":
        raise HTTPException(status_code=400, detail="รายการนี้ถูกจัดการไปแล้ว")

    # Logic การจัดการ
    if action_in.status == "APPROVED":
        # 1. เติมเงินให้ User
        user = db.query(User).filter(User.id == topup_req.user_id).with_for_update().first()
        old_balance = user.credit_balance
        user.credit_balance += topup_req.amount
        
        # 2. อัปเดตสถานะ
        topup_req.status = "APPROVED"
        topup_req.approved_by = current_user.id
        topup_req.approved_at = datetime.now()
        
        # 3. Log
        background_tasks.add_task(
            write_audit_log,
            user=current_user,
            action="APPROVE_TOPUP",
            target_id=topup_req.user_id, # เป้าหมายคือ User ที่ได้เงิน
            target_table="users",
            details={
                "amount": float(topup_req.amount),
                "req_id": str(topup_req.id),
                "old_balance": float(old_balance),
                "new_balance": float(user.credit_balance)
            },
            request=request
        )

    elif action_in.status == "REJECTED":
        topup_req.status = "REJECTED"
        topup_req.admin_remark = action_in.remark
        topup_req.approved_by = current_user.id
        topup_req.approved_at = datetime.now()

    db.commit()
    return {"status": "success", "message": f"Request {action_in.status}"}