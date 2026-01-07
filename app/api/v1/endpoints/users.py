from typing import List 
from uuid import UUID   
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
from sqlalchemy.orm import Session 
from app.models.user import UserRole, User
from app.schemas import UserCreate, MemberCreate, CreditAdjustment, UserResponse
from app.api import deps
from app.core.security import get_password_hash
from app.db.session import get_db
from app.core.audit_logger import write_audit_log


router = APIRouter()

@router.get("/me", response_model=UserResponse)
def read_user_me(
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    ดึงข้อมูลของ User ที่ Login อยู่ปัจจุบัน พร้อมชื่อร้าน
    """
    # ดึงชื่อร้าน ถ้ามี shop ผูกอยู่
    shop_name = current_user.shop.name if current_user.shop else None
    
    # แปลง User Model เป็น Dict แล้วเพิ่ม shop_name เข้าไป
    return {
        "id": current_user.id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "shop_id": current_user.shop_id,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at,
        "credit_balance": current_user.credit_balance,
        "shop_name": shop_name  # <--- ใส่ชื่อร้านตรงนี้
    }
# [เพิ่ม] API ดึงรายชื่อ Admin ของร้าน (สำหรับ Superadmin)
@router.get("/shop/{shop_id}/admins", response_model=List[UserResponse])
def read_shop_admins(
    shop_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    admins = db.query(User).filter(
        User.shop_id == shop_id,
        User.role == UserRole.admin
    ).all()
    return admins

# [เพิ่มใหม่] Superadmin สร้าง User ระดับ Admin ให้ร้านค้า
@router.post("/admins", response_model=UserResponse)
def create_shop_admin(
    user_in: UserCreate, # ใช้ Schema เดียวกัน แต่ต้องส่ง shop_id มาด้วย
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. Security: เฉพาะ Superadmin เท่านั้น
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. ต้องระบุ shop_id เสมอ (เพราะ Admin ต้องมีสังกัด)
    if not user_in.shop_id:
        raise HTTPException(status_code=400, detail="Shop ID is required for admin creation")

    # 3. เช็ค Username ซ้ำ
    if db.query(User).filter(User.username == user_in.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")

    # 4. สร้าง User เป็น Role Admin
    new_admin = User(
        username=user_in.username,
        password_hash=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        role=UserRole.admin,     # <--- กำหนดเป็น Admin
        shop_id=user_in.shop_id, # <--- ผูกกับร้านที่ส่งมา
        credit_balance=0,        # Admin ไม่ต้องมีเครดิตก็ได้ หรือจะใส่ให้เทสก็แล้วแต่
        is_active=True
    )
    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)
    return new_admin

# [เพิ่ม] API ลบ User (สำหรับ Superadmin ลบ Admin ร้าน)
@router.delete("/{user_id}")
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # ป้องกันการลบตัวเอง
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    db.delete(user)
    db.commit()
    return {"status": "success", "message": "User deleted"}

# 1. Admin สร้าง Member ใหม่
@router.post("/members", response_model=UserResponse)
def create_member(
    member_in: MemberCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security: เฉพาะ Admin/Superadmin
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Check User ซ้ำ
    if db.query(User).filter(User.username == member_in.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")

    # Auto Assign Shop ID
    target_shop_id = current_user.shop_id
    if not target_shop_id:
        raise HTTPException(status_code=400, detail="Admin has no shop")

    new_user = User(
        username=member_in.username,
        password_hash=get_password_hash(member_in.password),
        full_name=member_in.full_name,
        role=UserRole.member, # บังคับเป็น member
        shop_id=target_shop_id,
        credit_balance=0,
        is_active=True
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

# 2. Admin ดูรายชื่อ Member ในร้านตัวเอง
@router.get("/members", response_model=List[UserResponse])
def read_members(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if not current_user.shop_id:
        return []
        
    users = db.query(User).filter(
        User.shop_id == current_user.shop_id,
        User.role == UserRole.member
    ).offset(skip).limit(limit).all()
    
    return users

# 3. Admin เติม/ลด เครดิต
@router.post("/members/{user_id}/credit", response_model=UserResponse)
def adjust_credit(
    user_id: UUID,
    adjustment: CreditAdjustment,
    request: Request,               # <-- 1. รับ Request เพื่อเอา IP
    background_tasks: BackgroundTasks, # <-- 2. รับ BackgroundTasks
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security: ต้องเป็น Admin
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # หา User เป้าหมาย (ต้องอยู่ในร้านเดียวกันด้วย!)
    member = db.query(User).filter(
        User.id == user_id,
        User.shop_id == current_user.shop_id
    ).first()
    
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in your shop")

    # Update Credit (Decimal)
    # ควรระวัง: adjustment.amount ใน Schema ควรเป็น Decimal หรือ Int
    old_balance = member.credit_balance
    member.credit_balance += adjustment.amount
    
    # ป้องกันยอดติดลบ 
    if member.credit_balance < 0:
        db.rollback()
        raise HTTPException(status_code=400, detail="Credit balance cannot be negative")

    db.add(member)
    db.commit()
    db.refresh(member)
    
    # 3. ยิง Log เข้า Background Task (ไม่ถ่วงเวลา user)
    background_tasks.add_task(
        write_audit_log,
        
        user=current_user,
        action="ADJUST_CREDIT",
        target_id=member.id,
        target_table="users",
        details={
            "amount": float(adjustment.amount),
            "old_balance": float(old_balance),
            "new_balance": float(member.credit_balance),
            "note": adjustment.note
        },
        request=request
    )
    
    return member