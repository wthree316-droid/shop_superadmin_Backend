from typing import List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.api import deps
from app.db.session import get_db
from app.models.shop import Shop
from app.models.user import User, UserRole
from app.schemas import ShopCreate, ShopResponse, ShopConfigUpdate

router = APIRouter()

# [เพิ่มใหม่] API สำหรับ Admin ร้านค้า แก้ไขตั้งค่า LINE ของตัวเอง
@router.put("/config")
def update_shop_config(
    config_in: ShopConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security: ต้องเป็น Admin หรือ Superadmin
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # ต้องมีร้านสังกัด
    if not current_user.shop_id:
        raise HTTPException(status_code=400, detail="User has no shop")

    shop = db.query(Shop).filter(Shop.id == current_user.shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    # อัปเดตข้อมูล
    if config_in.line_channel_token is not None:
        shop.line_channel_token = config_in.line_channel_token
        
    if config_in.line_target_id is not None:
        shop.line_target_id = config_in.line_target_id

    db.commit()
    db.refresh(shop)
    
    return {"status": "success", "message": "Shop configuration updated"}

@router.post("/", response_model=ShopResponse)
def create_shop(
    shop_in: ShopCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security: เฉพาะ Superadmin เท่านั้น
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # เช็ค Code ซ้ำ
    if db.query(Shop).filter(Shop.code == shop_in.code).first():
        raise HTTPException(status_code=400, detail="Shop code already exists")

    new_shop = Shop(
        name=shop_in.name,
        code=shop_in.code,
        is_active=True
    )
    db.add(new_shop)
    db.commit()
    db.refresh(new_shop)
    return new_shop

@router.get("/", response_model=List[ShopResponse])
def read_shops(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Superadmin ดูได้ทุกร้าน
    if current_user.role == UserRole.superadmin:
        return db.query(Shop).offset(skip).limit(limit).all()
    
    # Admin ดูได้แค่ร้านตัวเอง (ข้อมูลตัวเอง)
    if current_user.shop_id:
        return db.query(Shop).filter(Shop.id == current_user.shop_id).all()
        
    return []

# 1. [แนะนำ] ระงับ/เปิดใช้งานร้านค้า (Soft Delete)
@router.patch("/{shop_id}/toggle_status")
def toggle_shop_status(
    shop_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security: เฉพาะ Superadmin
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")

    shop = db.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    # สลับสถานะ True <-> False
    shop.is_active = not shop.is_active
    db.commit()
    
    status_msg = "Activated" if shop.is_active else "Suspended"
    return {"status": "success", "message": f"Shop {status_msg}", "is_active": shop.is_active}

# 2. [ระวัง] ลบร้านค้าถาวร (Hard Delete) เอาไว้กันเหนียว
@router.delete("/{shop_id}")
def delete_shop_permanently(
    shop_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security: เฉพาะ Superadmin
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")

    shop = db.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    try:
        # ต้องไล่ลบข้อมูลลูกก่อน (เหมือนที่คุณทำใน system.py) แต่เพิ่มส่วน User และ Shop
        
        # 1. ลบรายการแทง และ โพย
        db.execute(text("DELETE FROM ticket_items WHERE ticket_id IN (SELECT id FROM tickets WHERE shop_id = :sid)"), {"sid": shop_id})
        db.execute(text("DELETE FROM tickets WHERE shop_id = :sid"), {"sid": shop_id})
        
        # 2. ลบประวัติการเงิน/เติมเงิน
        db.execute(text("DELETE FROM topup_requests WHERE shop_id = :sid"), {"sid": shop_id})
        db.execute(text("DELETE FROM shop_bank_accounts WHERE shop_id = :sid"), {"sid": shop_id})
        
        # 3. ลบ Logs
        db.execute(text("DELETE FROM audit_logs WHERE shop_id = :sid"), {"sid": shop_id})
        
        # 4. ลบหวยที่ร้านสร้างเอง
        db.execute(text("DELETE FROM number_risks WHERE lotto_type_id IN (SELECT id FROM lotto_types WHERE shop_id = :sid)"), {"sid": shop_id})
        db.execute(text("DELETE FROM lotto_results WHERE lotto_type_id IN (SELECT id FROM lotto_types WHERE shop_id = :sid)"), {"sid": shop_id})
        db.execute(text("DELETE FROM lotto_types WHERE shop_id = :sid"), {"sid": shop_id})

        # 5. ลบ Users ในร้าน
        db.execute(text("DELETE FROM users WHERE shop_id = :sid"), {"sid": shop_id})

        # 6. สุดท้าย... ลบร้าน
        db.delete(shop)
        
        db.commit()
        return {"status": "success", "message": f"Shop {shop.name} and all associated data have been deleted permanently."}

    except Exception as e:
        db.rollback()
        print(f"Delete Shop Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete shop. Data might be linked to other resources.")