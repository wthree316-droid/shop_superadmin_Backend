from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session, joinedload
from uuid import UUID
from datetime import datetime, date
from sqlalchemy import func
from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.shop import Shop 
# Import Model จากไฟล์ที่คุณส่งมา (app/models/topup.py)
from app.models.topup import TopupRequest, ShopBankAccount 
from app.schemas import (
    BankAccountCreate, BankAccountResponse,
    TopupCreate, TopupResponse, TopupAction
)
from app.core.audit_logger import write_audit_log
from app.core.notify import send_line_message
from app.core.config import settings

router = APIRouter()

# ==========================================
# 🏦 1. จัดการบัญชีธนาคารร้านค้า (Shop Bank Accounts)
# ==========================================

@router.post("/banks", response_model=BankAccountResponse)
def add_bank_account(
    bank_in: BankAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """Admin เพิ่มบัญชีรับเงินของร้าน"""
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if not current_user.shop_id:
        raise HTTPException(status_code=400, detail="User has no shop")

    # [✅ เพิ่ม] ลบขีดและช่องว่างออกให้เหลือแต่ตัวเลข
    clean_account_number = bank_in.account_number.replace("-", "").replace(" ", "")

    new_bank = ShopBankAccount(
        shop_id=current_user.shop_id,
        bank_name=bank_in.bank_name,
        account_name=bank_in.account_name,
        account_number=clean_account_number,
        is_active=True
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
    """ดูรายการบัญชีร้าน (Member ดูเพื่อโอนเงิน / Admin ดูเพื่อจัดการ)"""
    if not current_user.shop_id:
        return []
        
    return db.query(ShopBankAccount).filter(
        ShopBankAccount.shop_id == current_user.shop_id,
        ShopBankAccount.is_active == True
    ).all()

@router.delete("/banks/{bank_id}")
def delete_bank_account(
    bank_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """Admin ลบบัญชีธนาคาร"""
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    bank = db.query(ShopBankAccount).filter(
        ShopBankAccount.id == bank_id, 
        ShopBankAccount.shop_id == current_user.shop_id
    ).first()
    
    if not bank:
        raise HTTPException(status_code=404, detail="Bank account not found")
        
    db.delete(bank)
    db.commit()
    return {"status": "success", "message": "Bank account deleted"}


# ==========================================
# 💰 2. จัดการรายการเติมเงิน (Top-up Requests)
# ==========================================

@router.post("/requests", response_model=TopupResponse)
def create_topup_request(
    topup_in: TopupCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # [✅ เพิ่ม] Validation URL
    if topup_in.proof_image:
        # เช็คว่าเป็น URL และมาจาก Supabase ของเรา (ถ้าทำได้) หรืออย่างน้อยต้องเป็น http
        if not topup_in.proof_image.startswith("http"):
             raise HTTPException(status_code=400, detail="Invalid image URL format")
        
        # (Optional) ถ้าอยากเข้มงวด
        # if settings.SUPABASE_URL not in topup_in.proof_image:
        #      raise HTTPException(status_code=400, detail="อนุญาตเฉพาะรูปจากระบบเท่านั้น")
    """
    Member แจ้งเติมเงิน (แนบ URL รูปสลิปมาด้วย)
    """
    if not current_user.shop_id:
        raise HTTPException(status_code=400, detail="User not assigned to any shop")

    # 1. สร้างรายการ (Status = PENDING)
    new_req = TopupRequest(
        shop_id=current_user.shop_id,
        user_id=current_user.id,
        amount=topup_in.amount,
        proof_image=topup_in.proof_image, # รับ URL ที่ได้จาก Supabase
        status="PENDING"
    )
    db.add(new_req)
    db.commit()
    db.refresh(new_req)

    # 2. แจ้งเตือนเข้า LINE ของ Admin ร้าน
    try:
        shop = db.query(Shop).filter(Shop.id == current_user.shop_id).first()
        # เช็คว่าร้านตั้งค่า LINE หรือยัง
        if shop and shop.line_channel_token and shop.line_target_id:
            msg = f"💰 มีรายการแจ้งฝาก!\n" \
                  f"User: {current_user.username}\n" \
                  f"ยอดเงิน: {topup_in.amount:,.2f} บาท\n" \
                  f"เวลา: {datetime.now().strftime('%H:%M:%S')}"
            
            # ส่งเข้า Background Task เพื่อไม่ให้ API ช้า
            background_tasks.add_task(
                send_line_message,
                channel_token=shop.line_channel_token,
                target_id=shop.line_target_id,
                message=msg,
                image_url=topup_in.proof_image # ส่งรูปสลิปไปในไลน์ด้วย
            )
    except Exception as e:
        print(f"Line Notify Error: {e}")
    
    return new_req

@router.get("/requests", response_model=List[TopupResponse])
def get_topup_requests(
    status: str = "PENDING", # กรองดูเฉพาะรายการรอตรวจสอบได้
    skip: int = 0,
    limit: int = 50,
    start_date: Optional[date] = None, # [เพิ่ม]
    end_date: Optional[date] = None,   # [เพิ่ม]
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """ดูรายการแจ้งเติมเงิน"""
    query = db.query(TopupRequest).options(joinedload(TopupRequest.user))
    
    # แยกสิทธิ์การมองเห็น
    if current_user.role == UserRole.member:
        # Member เห็นแค่ของตัวเอง
        query = query.filter(TopupRequest.user_id == current_user.id)
    else:
        # Admin เห็นของทั้งร้าน
        query = query.filter(TopupRequest.shop_id == current_user.shop_id)
    
    # กรองสถานะ (ถ้าส่งมา)
    if status:
        query = query.filter(TopupRequest.status == status)

    # [✅ เพิ่มส่วนนี้] กรองตามช่วงเวลา
    if start_date:
        query = query.filter(func.date(TopupRequest.created_at) >= start_date)
    if end_date:
        query = query.filter(func.date(TopupRequest.created_at) <= end_date)

    # เรียงลำดับ ใหม่ -> เก่า
    results = query.order_by(TopupRequest.created_at.desc()).offset(skip).limit(limit).all()
    
    # Map username กลับไปให้ Frontend (เพราะใน DB เก็บแค่ ID)
    for r in results:
        r.username = r.user.username if r.user else "Unknown"
        
    return results

@router.put("/requests/{req_id}/action")
def process_topup_request(
    req_id: UUID,
    action_in: TopupAction, # รับค่า { status: "APPROVED"|"REJECTED", remark: "..." }
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """Admin กดยืนยัน หรือ ปฏิเสธ การเติมเงิน"""
    
    # Security Check
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Lock Row เพื่อป้องกันการกดซ้ำ (Race Condition)
    topup_req = db.query(TopupRequest).filter(TopupRequest.id == req_id).with_for_update().first()
    
    if not topup_req:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # เช็คว่าเป็นรายการของร้านตัวเองไหม
    if topup_req.shop_id != current_user.shop_id:
        raise HTTPException(status_code=403, detail="Cannot manage request from another shop")
        
    if topup_req.status != "PENDING":
        raise HTTPException(status_code=400, detail="รายการนี้ถูกจัดการไปแล้ว")

    # --- LOGIC การจัดการ ---
    
    if action_in.status == "APPROVED":
        # 1. เติมเงินเข้ากระเป๋า User
        user = db.query(User).filter(User.id == topup_req.user_id).with_for_update().first()
        old_balance = user.credit_balance
        user.credit_balance += topup_req.amount
        
        # 2. อัปเดตสถานะคำขอ
        topup_req.status = "APPROVED"
        topup_req.approved_by = current_user.id
        topup_req.approved_at = datetime.now()
        
        # 3. บันทึก Audit Log
        background_tasks.add_task(
            write_audit_log,
            user=current_user,
            action="APPROVE_TOPUP",
            target_id=topup_req.user_id,
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
        # ถ้าปฏิเสธ แค่อัปเดตสถานะและใส่เหตุผล
        topup_req.status = "REJECTED"
        topup_req.admin_remark = action_in.remark
        topup_req.approved_by = current_user.id
        topup_req.approved_at = datetime.now()
        
        background_tasks.add_task(
            write_audit_log,
            user=current_user,
            action="REJECT_TOPUP",
            target_id=topup_req.user_id,
            target_table="topup_requests",
            details={
                "req_id": str(topup_req.id), 
                "remark": action_in.remark
            },
            request=request
        )

    else:
        raise HTTPException(status_code=400, detail="Invalid status")

    db.commit()
    return {"status": "success", "message": f"Request {action_in.status}"}