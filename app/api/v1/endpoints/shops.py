from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.api import deps
from app.db.session import get_db
from app.models.shop import Shop
from app.models.user import User, UserRole
from app.schemas import ShopCreate, ShopUpdate, ShopResponse, ShopConfigUpdate
from sqlalchemy import func
from datetime import date, datetime, time, timedelta
from app.models.lotto import Ticket, TicketItem, TicketStatus
from app.models.lotto import LottoCategory

router = APIRouter()

@router.get("/config/{subdomain}")
def get_shop_config(subdomain: str, db: Session = Depends(get_db)):
    # 1. ค้นหาร้านจาก Subdomain
    shop = db.query(Shop).filter(Shop.subdomain == subdomain).first()
    
    # 2. ถ้าไม่เจอ หรือ ร้านถูกปิด/ลบ (Soft Delete) -> ระเบิด 404
    if not shop or not shop.is_active:
        raise HTTPException(status_code=404, detail="Shop not found")
        
    # 3. ถ้าเจอ ส่ง ID และ Config กลับไป
    return {
        "id": shop.id,
        "name": shop.name,
        "logo_url": shop.logo_url,
        "theme_color": shop.theme_color
    }

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
    
    # ✅ [เพิ่ม] อัปเดตข้อมูล Branding
    if config_in.logo_url is not None:
        shop.logo_url = config_in.logo_url
    
    if config_in.theme_color is not None:
        shop.theme_color = config_in.theme_color

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
    
    # 2.เช็ค Subdomain ซ้ำ (สำคัญมาก! ชื่อโดเมนซ้ำกันไม่ได้)
    if shop_in.subdomain:
        if db.query(Shop).filter(Shop.subdomain == shop_in.subdomain).first():
            raise HTTPException(status_code=400, detail=f"Subdomain '{shop_in.subdomain}' มีคนใช้แล้ว")

    new_shop = Shop(
        name=shop_in.name,
        code=shop_in.code,
        subdomain=shop_in.subdomain,
        is_active=True,
        theme_color="#2563EB"
    )
    db.add(new_shop)
    db.flush() # flush เพื่อให้ new_shop.id ถูกสร้างก่อน (ยังไม่ commit)

    # 2. ✅ [เพิ่มตรงนี้] สร้างหมวดหมู่พื้นฐานให้ร้านใหม่ทันที
    default_cats = [
        {"label": "หวยรัฐบาลไทย", "color": "#EF4444"},      # แดง
        {"label": "หวยฮานอย", "color": "#F59E0B"}, # ส้ม
        {"label": "หวยลาว", "color": "#10B981"},            # เขียว
        {"label": "หวยหุ้น", "color": "#EC4899"}, # ชมพู
        {"label": "หวยหุ้นVIP", "color": "#8B5CF6"},    # ม่วง
        {"label": "หวยดาวโจนส์", "color": "#F43F5E"},   # แดงเข้ม
        {"label": "หวยอื่นๆ", "color": "#3B82F6"},           # น้ำเงิน

    ]

    for cat in default_cats:
        new_cat = LottoCategory(
            label=cat["label"],
            color=cat["color"],
            shop_id=new_shop.id # ผูกกับร้านที่เพิ่งสร้าง
        )
        db.add(new_cat)
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

# API ดูยอดขายรายร้าน (รองรับช่วงเวลา)

@router.get("/stats/performance")
def get_shops_performance(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 1. กำหนดช่วงเวลา (Default: วันนี้)
    if start_date and end_date:
        try:
            s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            e_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            s_date = e_date = date.today()
    else:
        s_date = e_date = date.today()

    # 2. แปลงเป็น UTC Range (Start 00:00 - End 23:59 ของไทย)
    start_utc = datetime.combine(s_date, time.min) - timedelta(hours=7)
    end_utc = datetime.combine(e_date, time.max) - timedelta(hours=7)
    
    shops = db.query(Shop).order_by(Shop.created_at.desc()).all()
    results = []

    for shop in shops:
        # Base Filters (ร้าน + ช่วงเวลา)
        filters = [
            Ticket.shop_id == shop.id,
            Ticket.created_at >= start_utc,
            Ticket.created_at <= end_utc
        ]

        # A. ยอดขาย (Total Bet) - ไม่รวมบิลที่ยกเลิก
        sales = db.query(func.sum(Ticket.total_amount))\
            .filter(*filters, Ticket.status != TicketStatus.CANCELLED)\
            .scalar() or 0

        # B. ยอดจ่าย (Total Payout)
        payout = db.query(func.sum(TicketItem.winning_amount))\
            .join(Ticket)\
            .filter(
                *filters,
                TicketItem.status == 'WIN',
                Ticket.status != TicketStatus.CANCELLED
            ).scalar() or 0

        # ✅ C. ยอดรอผล (Pending) - เอาไว้หักออกจากกำไร
        pending = db.query(func.sum(Ticket.total_amount))\
            .filter(*filters, Ticket.status == TicketStatus.PENDING)\
            .scalar() or 0

        # ✅ D. ยอดที่ยกเลิก/คืน (Cancelled) - แสดงผลอย่างเดียว
        cancelled = db.query(func.sum(Ticket.total_amount))\
            .filter(*filters, Ticket.status == TicketStatus.CANCELLED)\
            .scalar() or 0

        # E. คำนวณกำไรสุทธิ (ตามสูตรใหม่: ยอดขาย - จ่าย - รอผล)
        profit = sales - payout - pending

        results.append({
            "id": str(shop.id),
            "name": shop.name,
            "code": shop.code,
            "logo_url": shop.logo_url,
            "sales": sales,
            "payout": payout,
            "pending": pending,     # ส่งยอดรอผลไปด้วยเผื่อใช้
            "cancelled": cancelled, # ส่งยอดยกเลิกไปแสดงผล
            "profit": profit,
            "is_active": shop.is_active
        })

    results.sort(key=lambda x: x['sales'], reverse=True)
    return results

@router.put("/{shop_id}", response_model=ShopResponse)
def update_shop(
    shop_id: UUID,
    shop_in: ShopUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. เช็คว่าเป็น Superadmin ไหม
    if current_user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. หาร้านที่จะแก้
    shop = db.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    # 3. เช็คซ้ำ (กรณีเปลี่ยนรหัสร้าน หรือ Subdomain)
    if shop_in.code and shop_in.code != shop.code:
        if db.query(Shop).filter(Shop.code == shop_in.code).first():
            raise HTTPException(status_code=400, detail="รหัสร้านนี้มีผู้ใช้แล้ว")
            
    if shop_in.subdomain and shop_in.subdomain != shop.subdomain:
        if db.query(Shop).filter(Shop.subdomain == shop_in.subdomain).first():
            raise HTTPException(status_code=400, detail="Subdomain นี้มีผู้ใช้แล้ว")

    # 4. อัปเดตข้อมูล
    update_data = shop_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(shop, field, value)

    db.add(shop)
    db.commit()
    db.refresh(shop)
    return shop