from typing import List 
from uuid import UUID   
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session 
from app.models.user import UserRole, User
from app.schemas import UserCreate, MemberCreate, CreditAdjustment, UserResponse, UserUpdate
from app.api import deps
from app.core.security import get_password_hash, create_access_token
from app.db.session import get_db
from datetime import timedelta
from app.core.config import settings
from app.models.shop import Shop

router = APIRouter()

# API สำหรับ Superadmin เพื่อขอล็อกอินเป็นร้านค้า (Impersonate)
@router.post("/impersonate/{shop_id}")
def impersonate_shop_admin(
    shop_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. เช็คว่าเป็น Superadmin
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. หา Admin ของร้าน
    shop_admin = db.query(User).filter(
        User.shop_id == shop_id,
        User.role == UserRole.admin,
        User.is_active == True
    ).first()

    if not shop_admin:
        raise HTTPException(status_code=404, detail="Shop has no active admin")
        
    # ดึงข้อมูลร้านค้าเพื่อเอา Subdomain
    shop = db.query(Shop).filter(Shop.id == shop_id).first()

    # 3. สร้าง Token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        subject=str(shop_admin.id), 
        role=shop_admin.role.value,
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "shop_subdomain": shop.subdomain,
        "user": {
            "username": shop_admin.username,
            "role": shop_admin.role,
            "shop_id": shop_admin.shop_id
        }
    }

@router.get("/me", response_model=UserResponse)
def read_user_me(
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    ดึงข้อมูลของ User ที่ Login อยู่ปัจจุบัน พร้อมชื่อร้าน
    """
    # ดึงชื่อร้าน ถ้ามี shop ผูกอยู่
    shop_name = current_user.shop.name if current_user.shop else None
    shop_logo = current_user.shop.logo_url if current_user.shop else None
    
    return {
        "id": current_user.id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "shop_id": current_user.shop_id,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at,
        "credit_balance": current_user.credit_balance,
        "shop_name": shop_name,
        "shop_logo": shop_logo
    }

# API ดึงรายชื่อ Admin ของร้าน (สำหรับ Superadmin)
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

# Superadmin สร้าง User ระดับ Admin ให้ร้านค้า
@router.post("/admins", response_model=UserResponse)
def create_shop_admin(
    user_in: UserCreate, 
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. Security: เฉพาะ Superadmin เท่านั้น
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. ต้องระบุ shop_id เสมอ
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
        role=UserRole.admin,     
        shop_id=user_in.shop_id, 
        credit_balance=0,        
        is_active=True
    )
    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)
    return new_admin

# API ลบ User
@router.delete("/{user_id}")
def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. หา User ที่ต้องการลบ
    user_to_delete = db.query(User).filter(User.id == user_id).first()
    if not user_to_delete:
        raise HTTPException(status_code=404, detail="User not found")

    # 2. ตรวจสอบสิทธิ์
    if current_user.role == UserRole.superadmin:
        pass 
        
    elif current_user.role == UserRole.admin:
        # Admin: ลบได้เฉพาะ Member และต้องเป็นคนในร้านตัวเอง
        if user_to_delete.role != UserRole.member:
             raise HTTPException(status_code=403, detail="Admins can only delete members")
             
        if user_to_delete.shop_id != current_user.shop_id:
             raise HTTPException(status_code=403, detail="Cannot delete member from another shop")
             
    else:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 3. ป้องกันการลบตัวเอง
    if user_to_delete.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    # 4. ทำการลบ
    try:
        db.delete(user_to_delete)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Cannot delete user with active history. Try banning instead.")

    return {"status": "success", "message": "User deleted"}

# Admin แก้ไขข้อมูล Member
@router.put("/members/{user_id}", response_model=UserResponse)
def update_member_by_admin(
    user_id: UUID,
    user_in: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    Admin รีเซ็ตรหัสผ่าน หรือแก้ไขข้อมูลให้ Member ในร้านตัวเอง
    """
    # 1. Security Check
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. หา Member เป้าหมาย
    member = db.query(User).filter(User.id == user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # 3. Data Isolation Check
    if current_user.role == UserRole.admin:
        if member.shop_id != current_user.shop_id:
             raise HTTPException(status_code=403, detail="Cannot update member from another shop")

    # 4. Logic การอัปเดต
    if user_in.username and user_in.username != member.username:
        if db.query(User).filter(User.username == user_in.username).first():
            raise HTTPException(status_code=400, detail="Username already taken")
        member.username = user_in.username

    if user_in.password:
        member.password_hash = get_password_hash(user_in.password)

    if user_in.full_name is not None:
        member.full_name = user_in.full_name

    if user_in.is_active is not None:
        member.is_active = user_in.is_active
        if user_in.is_active == True:
            member.failed_attempts = 0
            member.locked_until = None
        
    if user_in.commission_percent is not None:
        member.commission_percent = user_in.commission_percent

    db.add(member)
    db.commit()
    db.refresh(member)
    
    member.shop_name = member.shop.name if member.shop else None
    return member

# 1. Admin สร้าง Member ใหม่
@router.post("/members", response_model=UserResponse)
def create_member(
    member_in: MemberCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if db.query(User).filter(User.username == member_in.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")

    target_shop_id = current_user.shop_id
    if not target_shop_id:
        raise HTTPException(status_code=400, detail="Admin has no shop")

    new_user = User(
        username=member_in.username,
        password_hash=get_password_hash(member_in.password),
        full_name=member_in.full_name,
        role=UserRole.member, 
        shop_id=target_shop_id,
        credit_balance=0,
        is_active=True,
        commission_percent=member_in.commission_percent
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.put("/me", response_model=UserResponse)
def update_user_me(
    user_in: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    ให้ Member แก้ไขข้อมูลส่วนตัว
    """
    if user_in.username and user_in.username != current_user.username:
        existing_user = db.query(User).filter(User.username == user_in.username).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already taken")
        current_user.username = user_in.username

    if user_in.password:
        current_user.password_hash = get_password_hash(user_in.password)

    if user_in.full_name is not None:
        current_user.full_name = user_in.full_name

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    
    current_user.shop_name = current_user.shop.name if current_user.shop else None
    return current_user

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

# 3. Admin เติม/ลด เครดิต (Manual Top-up Logic)
@router.post("/members/{user_id}/credit", response_model=UserResponse)
def adjust_credit(
    user_id: UUID,
    adjustment: CreditAdjustment,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security: ต้องเป็น Admin หรือ Superadmin
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # ✅ [CRITICAL FIX] ใช้ with_for_update() เพื่อ Lock แถวข้อมูลป้องกัน Race Condition
    member = db.query(User).filter(
        User.id == user_id,
        User.shop_id == current_user.shop_id
    ).with_for_update().first() 
    
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in your shop")

    # Update Credit
    member.credit_balance += adjustment.amount
    
    # ป้องกันยอดติดลบ (กรณีหักเงิน)
    if member.credit_balance < 0:
        db.rollback() # สำคัญ: ต้อง Rollback Transaction ที่ Lock ไว้
        raise HTTPException(status_code=400, detail="Credit balance cannot be negative")

    db.add(member)
    db.commit()
    db.refresh(member)
    
    return member

# ✅ เพิ่ม Endpoint ใหม่สำหรับ Toggle Status
@router.patch("/{user_id}/toggle-status")
def toggle_user_status(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. Security Check
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. Find User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 3. Check Ownership (Admin can only toggle own members)
    if current_user.role == UserRole.admin:
        if user.shop_id != current_user.shop_id:
            raise HTTPException(status_code=403, detail="Cannot modify user from another shop")
        if user.role != UserRole.member:
            raise HTTPException(status_code=403, detail="Admins can only toggle members")

    # 4. Toggle Status
    user.is_active = not user.is_active
    
    # ถ้าเปิดใช้งาน ให้รีเซ็ตค่า failed_attempts ด้วย
    if user.is_active:
        user.failed_attempts = 0
        user.locked_until = None

    db.commit()
    
    return {"status": "success", "is_active": user.is_active, "message": "User status updated"}
