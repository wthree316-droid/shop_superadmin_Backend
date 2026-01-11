from typing import List 
from uuid import UUID   
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
from sqlalchemy.orm import Session 
from app.models.user import UserRole, User
from app.schemas import UserCreate, MemberCreate, CreditAdjustment, UserResponse, UserUpdate
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

# API ลบ User (สำหรับ Superadmin ลบ Admin ร้าน -> admin ลบ member ในร้าน)
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

    # 2. ตรวจสอบสิทธิ์ (Logic ใหม่)
    if current_user.role == UserRole.superadmin:
        # Superadmin ลบได้หมด (ยกเว้นตัวเอง)
        pass 
        
    elif current_user.role == UserRole.admin:
        # Admin: ลบได้เฉพาะ Member และต้องเป็นคนในร้านตัวเอง
        if user_to_delete.role != UserRole.member:
             raise HTTPException(status_code=403, detail="Admins can only delete members")
             
        if user_to_delete.shop_id != current_user.shop_id:
             raise HTTPException(status_code=403, detail="Cannot delete member from another shop")
             
    else:
        # Member ไม่มีสิทธิ์ลบใคร
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
        # กรณีลบไม่ได้เพราะติด Foreign Key (เช่น เคยซื้อหวยไว้)
        raise HTTPException(status_code=400, detail="Cannot delete user with active history. Try banning instead.")

    return {"status": "success", "message": "User deleted"}

# Admin แก้ไขข้อมูล Member (เช่น รีเซ็ตรหัสผ่านให้กรณีลืม)
@router.put("/members/{user_id}", response_model=UserResponse)
def update_member_by_admin(
    user_id: UUID,
    user_in: UserUpdate, # ใช้ Schema เดียวกับตอนแก้ตัวเอง
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    Admin รีเซ็ตรหัสผ่าน หรือแก้ไขข้อมูลให้ Member ในร้านตัวเอง
    """
    # 1. Security Check: คนทำรายการต้องเป็น Admin หรือ Superadmin
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. หา Member เป้าหมาย
    member = db.query(User).filter(User.id == user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # 3. Data Isolation Check: ต้องอยู่ร้านเดียวกัน (ยกเว้น Superadmin)
    if current_user.role == UserRole.admin:
        if member.shop_id != current_user.shop_id:
             raise HTTPException(status_code=403, detail="Cannot update member from another shop")

    # 4. Logic การอัปเดต (คล้ายกับ update_user_me)
    
    # 4.1 แก้ Username (ต้องเช็คซ้ำ)
    if user_in.username and user_in.username != member.username:
        if db.query(User).filter(User.username == user_in.username).first():
            raise HTTPException(status_code=400, detail="Username already taken")
        member.username = user_in.username

    # 4.2 แก้รหัสผ่าน (Reset Password) -> สำคัญมากต้อง Hash ใหม่
    if user_in.password:
        member.password_hash = get_password_hash(user_in.password)

    # 4.3 แก้ชื่อ
    if user_in.full_name is not None:
        member.full_name = user_in.full_name

    # 4.4 แก้สถานะ (เช่น ปลดแบน/แบน User)
    if user_in.is_active is not None:
        member.is_active = user_in.is_active

    db.add(member)
    db.commit()
    db.refresh(member)
    
    # ใส่ shop_name กลับไปให้ครบตาม Schema Response
    member.shop_name = member.shop.name if member.shop else None
    
    return member

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

@router.put("/me", response_model=UserResponse)
def update_user_me(
    user_in: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    """
    ให้ Member แก้ไขข้อมูลส่วนตัว (เปลี่ยนรหัสผ่าน, ชื่อ-นามสกุล, หรือ Username)
    """
    # 1. ถ้ามีการแก้ Username ต้องเช็คก่อนว่าซ้ำคนอื่นไหม
    if user_in.username and user_in.username != current_user.username:
        existing_user = db.query(User).filter(User.username == user_in.username).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already taken")
        current_user.username = user_in.username

    # 2. ถ้ามีการแก้ Password ต้อง Hash ใหม่
    if user_in.password:
        current_user.password_hash = get_password_hash(user_in.password)

    # 3. แก้ Full Name
    if user_in.full_name is not None:
        current_user.full_name = user_in.full_name

    # หมายเหตุ: เราจะไม่ update 'is_active' หรือ 'role' จาก endpoint นี้เพื่อความปลอดภัย
    # แม้ใน Schema UserUpdate จะมี field พวกนั้นก็ตาม

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    
    # เพิ่ม shop_name กลับไปให้ response (เหมือน read_user_me)
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