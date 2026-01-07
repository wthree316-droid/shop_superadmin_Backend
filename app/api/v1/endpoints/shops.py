from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.db.session import get_db
from app.models.shop import Shop
from app.models.user import User, UserRole
from app.schemas import ShopCreate, ShopResponse

router = APIRouter()

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