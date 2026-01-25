from decimal import Decimal
from typing import List, Optional, Any, Dict
from datetime import datetime, time, date, timedelta
from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
from sqlalchemy import func, case, desc, extract, String
from pydantic import BaseModel

from app.api import deps
from app.schemas import (
    TicketCreate, TicketResponse, 
    LottoCreate, LottoResponse,
    RateProfileCreate, RateProfileResponse,
    NumberRiskCreate, NumberRiskResponse,
    BulkRateRequest, CategoryCreate, CategoryResponse, BulkRiskCreate
)
from app.db.session import get_db
from app.models.lotto import Ticket, TicketItem, LottoType, TicketStatus, RateProfile, NumberRisk, LottoCategory
from app.models.user import User, UserRole
from app.core import lotto_cache
from app.core.game_logic import expand_numbers
from app.core.risk_cache import get_cached_risks, invalidate_cache

from supabase import create_client, Client
from app.core.config import settings, get_thai_now

router = APIRouter()

DEFAULT_CATEGORIES_CONFIG = [
    {"label": "หวยรัฐบาลไทย", "color": "#EF4444"},
    {"label": "หวยฮานอย", "color": "#F59E0B"},
    {"label": "หวยลาว", "color": "#10B981"},
    {"label": "หวยหุ้น", "color": "#EC4899"},
    {"label": "หวยหุ้นVIP", "color": "#8B5CF6"},
    {"label": "หวยดาวโจนส์", "color": "#F43F5E"},
    {"label": "หวยอื่นๆ", "color": "#3B82F6"},
]

try:
    supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    BUCKET_NAME = "lotto_images"
except Exception as e:
    print(f"Supabase Init Error: {e}")

# --- APIs ---

@router.get("/rates", response_model=List[RateProfileResponse])
def get_rate_profiles(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    return db.query(RateProfile).filter(
        (RateProfile.shop_id == current_user.shop_id) | (RateProfile.shop_id == None)
    ).all()

@router.post("/rates", response_model=RateProfileResponse)
def create_rate_profile(
    profile_in: RateProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    shop_id = current_user.shop_id if current_user.role == UserRole.admin else None

    new_profile = RateProfile(
        name=profile_in.name, 
        rates=profile_in.rates,
        shop_id=shop_id
    )
    db.add(new_profile)
    db.commit()
    db.refresh(new_profile)
    return new_profile

# ค้นหา @router.get("/categories" ...
@router.get("/categories", response_model=List[CategoryResponse])
def get_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # ✅ แก้ไข Logic การดึงหมวดหมู่ (Fix Data Isolation)
    # 1. Admin ร้านค้า: เห็นเฉพาะของร้านตัวเอง (shop_id ตรงกัน) + หมวดกลาง (shop_id เป็น NULL)
    if current_user.role == UserRole.admin:
        if not current_user.shop_id:
             return [] # ถ้าไม่มี Shop ID ไม่ให้เห็นอะไรเลยกันพลาด
             
        query = db.query(LottoCategory).filter(
            (LottoCategory.shop_id == current_user.shop_id) | 
            (LottoCategory.shop_id == None) # หมวดกลาง (ถ้าไม่ต้องการให้เห็นหมวดกลาง ลบบรรทัดนี้ออก)
        )
        return query.order_by(LottoCategory.order_index.asc()).all()
    
    # 2. Superadmin: เห็นทั้งหมด
    elif current_user.role == UserRole.superadmin:
        return db.query(LottoCategory).order_by(LottoCategory.shop_id, LottoCategory.order_index.asc()).all()
        
    # 3. Member: (ปกติต้องดึงตามร้านที่เล่น) - อันนี้เผื่อไว้
    else:
        # สมมติว่า Member เห็นหมวดกลางไปก่อน หรือต้องส่ง shop_id มาเพื่อ filter
        return db.query(LottoCategory).filter(LottoCategory.shop_id == None).order_by(LottoCategory.order_index.asc()).all()

# ค้นหา @router.post("/categories" ...
@router.post("/categories", response_model=CategoryResponse)
def create_category(
    cat_in: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    # ✅ บังคับใส่ Shop ID เสมอ ถ้าเป็น Admin ร้าน
    target_shop_id = None
    if current_user.role == UserRole.admin:
        target_shop_id = current_user.shop_id
    elif current_user.role == UserRole.superadmin:
        # Superadmin อาจจะสร้างให้ร้านอื่นได้ (ถ้ามี logic รับ shop_id) แต่เบื้องต้นให้เป็น NULL (Global) หรือใส่ของตัวเอง
        target_shop_id = current_user.shop_id # หรือ None ถ้าต้องการสร้าง Global Category

    new_cat = LottoCategory(
        label=cat_in.label,
        color=cat_in.color,
        shop_id=target_shop_id, # ✅ ใช้ตัวแปรที่เช็คแล้ว
        order_index=getattr(cat_in, 'order_index', 999)
    )
    db.add(new_cat)
    db.commit()
    db.refresh(new_cat)
    return new_cat

@router.post("/categories/init_defaults")
def init_default_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if not current_user.shop_id:
        raise HTTPException(status_code=400, detail="User has no shop")

    existing_count = db.query(LottoCategory).filter(
        LottoCategory.shop_id == current_user.shop_id
    ).count()

    added_count = 0
    for default_cat in DEFAULT_CATEGORIES_CONFIG:
        exists = db.query(LottoCategory).filter(
            LottoCategory.shop_id == current_user.shop_id,
            LottoCategory.label == default_cat["label"]
        ).first()

        if not exists:
            new_cat = LottoCategory(
                label=default_cat["label"],
                color=default_cat["color"],
                shop_id=current_user.shop_id
            )
            db.add(new_cat)
            added_count += 1
    
    db.commit()
    return {"message": f"เพิ่มหมวดหมู่สำเร็จ {added_count} รายการ", "added": added_count}

@router.post("/categories", response_model=CategoryResponse)
def create_category(
    cat_in: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    new_cat = LottoCategory(
        label=cat_in.label,
        color=cat_in.color,
        shop_id=current_user.shop_id,
        order_index=getattr(cat_in, 'order_index', 999)
    )
    db.add(new_cat)
    db.commit()
    db.refresh(new_cat)
    return new_cat

@router.get("/lottos", response_model=List[LottoResponse])
def get_lottos(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    def fetch_all_lottos():
        return db.query(LottoType).order_by(LottoType.id).all()

    all_lottos = lotto_cache.get_cached_lottos(fetch_all_lottos)
    filtered_lottos = []
    
    for lotto in all_lottos:
        lotto_shop_id = str(lotto.get('shop_id')) if lotto.get('shop_id') else None
        user_shop_id = str(current_user.shop_id) if current_user.shop_id else None

        if current_user.role == UserRole.member:
            if lotto.get('is_active') is True and lotto.get('is_template') is False:
                if user_shop_id:
                    if lotto_shop_id == user_shop_id:
                        filtered_lottos.append(lotto)
                else:
                    filtered_lottos.append(lotto)
                    
        elif current_user.role == UserRole.admin:
            if lotto_shop_id == user_shop_id:
                filtered_lottos.append(lotto)
        
        else:
            filtered_lottos.append(lotto)
            
    return filtered_lottos

@router.put("/categories/{cat_id}", response_model=CategoryResponse)
def update_category(
    cat_id: UUID,
    cat_in: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    category = db.query(LottoCategory).filter(LottoCategory.id == cat_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    if current_user.role == UserRole.admin:
        if category.shop_id is not None and category.shop_id != current_user.shop_id:
            raise HTTPException(status_code=403, detail="Access denied")

    category.label = cat_in.label
    category.color = cat_in.color

    if hasattr(cat_in, 'order_index'):
        category.order_index = cat_in.order_index
        
    db.commit()
    db.refresh(category)
    return category

# แก้ไข API ลบหมวดหมู่ (ให้ลบได้แม้มีหวยอยู่ หรือเป็นหมวดกลาง)
@router.delete("/categories/{cat_id}")
def delete_category(
    cat_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    category = db.query(LottoCategory).filter(LottoCategory.id == cat_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    # 1. เช็คสิทธิ์เจ้าของ (ถ้าเป็น Admin ร้าน ลบของร้านอื่นไม่ได้)
    # แต่ถ้า category.shop_id เป็น None (หมวดกลาง) เราจะยอมให้ลบได้ (ในมุมมองของร้านคือซ่อน หรือย้ายหวยหนี)
    if current_user.role == UserRole.admin:
        if category.shop_id is not None and category.shop_id != current_user.shop_id:
            raise HTTPException(status_code=403, detail="คุณลบหมวดหมู่ของร้านอื่นไม่ได้")

    # 2. ✅ แก้ใหม่: ถ้ามีหวยอยู่ในหมวดนี้ ให้ย้ายหวยพวกนั้นไป "หมวดอื่นๆ" (หรือ NULL) ก่อนลบ
    # หาหวยที่ใช้หมวดหมู่นี้อยู่
    lottos_in_category = db.query(LottoType).filter(LottoType.category == str(cat_id)).all()
    
    if lottos_in_category:
        # ย้ายหวยทั้งหมดไปหมวด "General" หรือ "อื่นๆ" (หรือปล่อยว่าง)
        # ลองหาหมวด General ของร้านนี้ดู
        general_cat = db.query(LottoCategory).filter(
            LottoCategory.shop_id == current_user.shop_id,
            LottoCategory.label.in_(["อื่นๆ", "General", "ทั่วไป"])
        ).first()

        new_cat_id = str(general_cat.id) if general_cat else "General" # ถ้าหาไม่เจอใส่เป็น Text ไปก่อน

        for lotto in lottos_in_category:
            # ถ้าเป็น Admin ร้าน ย้ายเฉพาะหวยร้านตัวเอง
            if current_user.role == UserRole.admin and lotto.shop_id != current_user.shop_id:
                continue 
            lotto.category = new_cat_id
        
        db.commit() # บันทึกการย้ายก่อน

    # 3. ลบหมวดหมู่
    try:
        db.delete(category)
        db.commit()
    except Exception as e:
        db.rollback()
        # กรณีลบไม่ได้จริงๆ (เช่น เป็นหมวดกลางที่ร้านอื่นใช้อยู่ด้วย)
        # เราอาจจะต้องใช้วิธีอื่น แต่เบื้องต้นแจ้ง Error ไปก่อน
        print(f"Delete Error: {e}")
        raise HTTPException(status_code=400, detail="ไม่สามารถลบหมวดหมู่นี้ได้ (อาจเป็นหมวดหมู่ระบบ)")
    
    return {"status": "success", "message": "ลบหมวดหมู่และย้ายหวยที่เกี่ยวข้องเรียบร้อยแล้ว"}

def parse_time(t_str: str):
    if not t_str: return None
    try:
        # Handle "HH:MM" format by appending ":00"
        if len(t_str) == 5: t_str += ":00"
        return datetime.strptime(t_str, "%H:%M:%S").time()
    except ValueError:
        return None
    
@router.post("/lottos", response_model=LottoResponse)
def create_lotto(
    lotto_in: LottoCreate, 
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    is_template = getattr(lotto_in, "is_template", False)
    shop_id = None
    
    if current_user.role == UserRole.admin:
        shop_id = current_user.shop_id
        is_template = False

    existing_lotto = db.query(LottoType).filter(
        LottoType.code == lotto_in.code,
        LottoType.shop_id == shop_id
    ).first()

    if existing_lotto:
        raise HTTPException(status_code=400, detail=f"รหัสหวย {lotto_in.code} มีอยู่แล้ว")

    new_lotto = LottoType(
        name=lotto_in.name,
        code=lotto_in.code,
        category=lotto_in.category,
        rate_profile_id=lotto_in.rate_profile_id,
        img_url=lotto_in.img_url,
        api_link=lotto_in.api_link,
        open_days=lotto_in.open_days,
        open_time=parse_time(lotto_in.open_time),
        close_time=parse_time(lotto_in.close_time),
        result_time=parse_time(lotto_in.result_time),
        is_active=True,
        shop_id=shop_id,
        is_template=is_template
    )
    db.add(new_lotto)
    db.commit()
    lotto_cache.invalidate_lotto_cache()
    db.refresh(new_lotto)
    return new_lotto

@router.put("/lottos/bulk-rate-update")
def bulk_update_lotto_rates(
    body: BulkRateRequest, 
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        query = db.query(LottoType).filter(LottoType.is_template == False)

        if current_user.role == UserRole.admin:
            query = query.filter(LottoType.shop_id == current_user.shop_id)

        updated_count = query.update(
            {LottoType.rate_profile_id: body.rate_profile_id},
            synchronize_session=False
        )
        
        db.commit()
        return {"message": "Success", "updated_count": updated_count}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดในการอัปเดตข้อมูล")

@router.put("/lottos/{lotto_id}", response_model=LottoResponse)
def update_lotto(
    lotto_id: UUID,
    lotto_in: LottoCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    lotto = db.query(LottoType).filter(LottoType.id == lotto_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Not found")

    if lotto_in.img_url and lotto.img_url and lotto_in.img_url != lotto.img_url:
        try:
            old_file_name = lotto.img_url.split("/")[-1]
            supabase.storage.from_(BUCKET_NAME).remove([old_file_name])
        except Exception:
            pass

    lotto.name = lotto_in.name
    lotto.code = lotto_in.code
    lotto.category = lotto_in.category
    lotto.rate_profile_id = lotto_in.rate_profile_id
    lotto.img_url = lotto_in.img_url
    lotto.api_link = lotto_in.api_link
    lotto.open_days = lotto_in.open_days
    lotto.open_time = parse_time(lotto_in.open_time)
    lotto.close_time = parse_time(lotto_in.close_time)
    lotto.result_time = parse_time(lotto_in.result_time)
    
    db.commit()
    lotto_cache.invalidate_lotto_cache()
    db.refresh(lotto)
    return lotto

@router.patch("/lottos/{lotto_id}/toggle")
def toggle_lotto_status(
    lotto_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    lotto = db.query(LottoType).filter(LottoType.id == lotto_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto not found")

    lotto.is_active = not lotto.is_active
    db.commit()
    return {"status": "success", "new_state": lotto.is_active}

@router.delete("/lottos/{lotto_id}")
def delete_lotto(
    lotto_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    lotto = db.query(LottoType).filter(LottoType.id == lotto_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto not found")
    
    try:
        db.delete(lotto)
        db.commit()
        lotto_cache.invalidate_lotto_cache()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="ไม่สามารถลบหวยนี้ได้")
    
    return {"status": "success", "message": "Lotto deleted successfully"}

@router.get("/lottos/templates", response_model=List[LottoResponse])
def get_lotto_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    return db.query(LottoType).filter(LottoType.is_template == True).all()

@router.post("/lottos/import_defaults")
def import_default_lottos(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role != UserRole.admin or not current_user.shop_id:
        raise HTTPException(status_code=403, detail="Only Shop Admin can import")

    templates = db.query(LottoType).filter(LottoType.is_template == True).all()
    if not templates:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลแม่แบบจากระบบกลาง")

    default_rate = db.query(RateProfile).filter(
        RateProfile.shop_id == current_user.shop_id
    ).first()
    
    if not default_rate:
         raise HTTPException(status_code=400, detail="กรุณาสร้าง 'เรทราคา' ในร้านค้าของคุณก่อนกดดึงข้อมูล")
    
    imported_count = 0
    for tmpl in templates:
        exists = db.query(LottoType).filter(
            LottoType.shop_id == current_user.shop_id,
            LottoType.code == tmpl.code
        ).first()
        
        if not exists:
            new_lotto = LottoType(
                name=tmpl.name,
                code=tmpl.code,
                category=tmpl.category,
                img_url=tmpl.img_url,
                api_link=tmpl.api_link,
                open_days=tmpl.open_days,
                open_time=tmpl.open_time,
                close_time=tmpl.close_time,
                result_time=tmpl.result_time,
                is_active=True,
                is_template=False,
                shop_id=current_user.shop_id,
                rate_profile_id=default_rate.id
            )
            db.add(new_lotto)
            imported_count += 1
    
    db.commit()
    return {"message": f"ดึงข้อมูลสำเร็จ! เพิ่มหวยใหม่ {imported_count} รายการ"}

# --- Risk Management ---
@router.post("/risks/batch")
def create_bulk_risks(
    payload: BulkRiskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # ตรวจสอบสิทธิ์ (Admin เท่านั้น)
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    count = 0
    
    # ใช้ Transaction (ถ้าพังตัวนึง ให้ Rollback ทั้งหมด หรือจะข้ามก็ได้ แล้วแต่ Design)
    try:
        for item in payload.items:
            # เช็คว่ามีอยู่แล้วไหม (Optional: ถ้ามีแล้วข้าม หรือ Update)
            existing = db.query(NumberRisk).filter(
                NumberRisk.lotto_type_id == payload.lotto_type_id,
                NumberRisk.number == item.number,
                NumberRisk.specific_bet_type == item.specific_bet_type,
                # NumberRisk.date == ... (ถ้ามี field วันที่)
            ).first()

            if not existing:
                new_risk = NumberRisk(
                    lotto_type_id=payload.lotto_type_id,
                    number=item.number,
                    specific_bet_type=item.specific_bet_type,
                    risk_type=payload.risk_type,
                    shop_id=current_user.shop_id, # บันทึกว่าเป็นของร้านไหน
                    created_by=current_user.id
                )
                db.add(new_risk)
                count += 1
            else:
                # ถ้ามีอยู่แล้ว อาจจะแค่อัปเดตสถานะ (เช่นจาก HALF เป็น CLOSE)
                existing.risk_type = payload.risk_type
        
        db.commit()
        return {"message": "success", "inserted": count}

    except Exception as e:
        db.rollback()
        print(e)
        raise HTTPException(status_code=500, detail="Database error during bulk insert")

@router.get("/risks/{lotto_id}", response_model=List[NumberRiskResponse])
def get_risks(
    lotto_id: UUID,
    date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            target_date = (datetime.utcnow() + timedelta(hours=7)).date()
    else:
        target_date = (datetime.utcnow() + timedelta(hours=7)).date()

    start_utc = datetime.combine(target_date, time.min) - timedelta(hours=7)
    end_utc = datetime.combine(target_date, time.max) - timedelta(hours=7)

    return db.query(NumberRisk).filter(
        NumberRisk.lotto_type_id == lotto_id,
        NumberRisk.created_at >= start_utc,
        NumberRisk.created_at <= end_utc
    ).all()

@router.post("/risks", response_model=NumberRiskResponse)
def add_risk(
    risk_in: NumberRiskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    existing = db.query(NumberRisk).filter(
        NumberRisk.lotto_type_id == risk_in.lotto_type_id,
        NumberRisk.number == risk_in.number,
        NumberRisk.specific_bet_type == risk_in.specific_bet_type
    ).first()

    if existing:
        existing.risk_type = risk_in.risk_type
        db.commit()
        db.refresh(existing)
        return existing

    new_risk = NumberRisk(
        lotto_type_id=risk_in.lotto_type_id,
        number=risk_in.number,
        risk_type=risk_in.risk_type,
        specific_bet_type=risk_in.specific_bet_type
    )
    db.add(new_risk)
    db.commit()
    db.refresh(new_risk)
    invalidate_cache(str(risk_in.lotto_type_id))
    return new_risk

@router.delete("/risks/{risk_id}")
def delete_risk(
    risk_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    risk = db.query(NumberRisk).filter(NumberRisk.id == risk_id).first()
    if risk:
        lotto_id = str(risk.lotto_type_id)
        db.delete(risk)
        db.commit()
        invalidate_cache(lotto_id)
        
    return {"status": "deleted"}

# --- Submit Ticket (แก้ใหม่: รับเลขปิดได้ แต่คิดเงิน 0 บาท) ---
@router.post("/submit_ticket", response_model=TicketResponse)
def submit_ticket(
    ticket_in: TicketCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. จัดการเรื่อง Shop ID
    target_shop_id = current_user.shop_id
    if current_user.role == UserRole.superadmin:
        if ticket_in.shop_id:
            target_shop_id = ticket_in.shop_id
    elif current_user.role == UserRole.admin:
        target_shop_id = current_user.shop_id

    # 2. ตรวจสอบหวย
    lotto = db.query(LottoType).filter(LottoType.id == ticket_in.lotto_type_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto type not found")
    
    # 3. คำนวณงวดวันที่ (ใช้ Logic เดิมของคุณ)
    now_thai = get_thai_now()
    target_round_date = now_thai.date()
    
    rules = lotto.rules or {}
    schedule_type = rules.get('schedule_type', 'weekly')

    if schedule_type == 'monthly':
        close_dates = rules.get('close_dates', [1, 16])
        target_dates = sorted([int(d) for d in close_dates])
        current_day = now_thai.day
        found_date = -1
        for d in target_dates:
            if d > current_day:
                found_date = d
                break
            if d == current_day:
                if lotto.close_time:
                    close_h, close_m = map(int, str(lotto.close_time)[:5].split(':'))
                    close_dt = now_thai.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
                    if now_thai < close_dt:
                        found_date = d
                        break
        if found_date == -1:
            found_date = target_dates[0]
            next_month = now_thai.replace(day=28) + timedelta(days=4) 
            target_round_date = date(next_month.year, next_month.month, found_date)
        else:
            target_round_date = date(now_thai.year, now_thai.month, found_date)
    else:
        if lotto.close_time:
            close_h, close_m = map(int, str(lotto.close_time)[:5].split(':'))
            close_dt = now_thai.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
            if now_thai > close_dt:
                target_round_date = target_round_date + timedelta(days=1)

    # =========================================================
    # 🔥 4. เตรียมเลขอั้น (Risk) มาเช็ค (Logic วันต่อวัน)
    # =========================================================
    r_start = datetime.combine(target_round_date, time.min) - timedelta(hours=7)
    r_end = datetime.combine(target_round_date, time.max) - timedelta(hours=7)

    daily_risks = db.query(NumberRisk).filter(
        NumberRisk.lotto_type_id == ticket_in.lotto_type_id,
        NumberRisk.created_at >= r_start,
        NumberRisk.created_at <= r_end
    ).all()

    risk_map = {}
    for r in daily_risks:
        risk_map[f"{r.number}:{r.specific_bet_type}"] = r.risk_type
        risk_map[f"{r.number}:ALL"] = r.risk_type

    # เตรียม Rate Profile
    rates = {}
    if lotto.rate_profile:
        rates = lotto.rate_profile.rates

    # =========================================================
    # 🔥 5. คำนวณยอดเงินจริง (ตัดเลขปิดออก) และเตรียมรายการ
    # =========================================================
    processed_items = []
    total_amount = Decimal(0)

    for item_in in ticket_in.items:
        # 5.1 เช็คสถานะความเสี่ยง
        check_key = f"{item_in.number}:{item_in.bet_type}"
        check_key_all = f"{item_in.number}:ALL"
        risk_status = risk_map.get(check_key) or risk_map.get(check_key_all)

        # 5.2 ดึงเรทจ่ายมาตรฐาน
        rate_config = rates.get(item_in.bet_type, {})
        if isinstance(rate_config, (int, float, str, Decimal)):
            base_pay = Decimal(str(rate_config))
            min_bet = Decimal("1")
            max_bet = Decimal("100000")
        else:
            base_pay = Decimal(str(rate_config.get('pay', 0)))
            min_bet = Decimal(str(rate_config.get('min', 1)))
            max_bet = Decimal(str(rate_config.get('max', 0)))

        # กำหนดตัวแปรที่จะบันทึก
        final_amount = item_in.amount
        final_rate = base_pay
        
        # --- LOGIC จัดการเลขอั้น ---
        if risk_status == "CLOSE":
            # ✅ ถ้าปิด: รับเข้าโพยได้ แต่ปรับยอดเงินและเรทเป็น 0
            final_amount = Decimal(0)
            final_rate = Decimal(0)
        elif risk_status == "HALF":
            # ✅ ถ้าครึ่ง: จ่ายครึ่งเดียว
            final_rate = base_pay / 2
        else:
            # ✅ ปกติ: เช็ค Limit (ถ้าเลขปิด เราไม่เช็ค Limit เพราะมัน 0 บาทอยู่แล้ว)
            if base_pay == 0:
                 raise HTTPException(status_code=400, detail=f"ไม่พบอัตราจ่ายสำหรับ: {item_in.bet_type}")
            if item_in.amount < min_bet:
                raise HTTPException(status_code=400, detail=f"แทงขั้นต่ำ {min_bet:,.0f} บาท ({item_in.bet_type})")
            if max_bet > 0 and item_in.amount > max_bet:
                raise HTTPException(status_code=400, detail=f"แทงสูงสุด {max_bet:,.0f} บาท ({item_in.bet_type})")

        # เพิ่มเข้าลิสต์เตรียมบันทึก
        processed_items.append({
            "number": item_in.number,
            "bet_type": item_in.bet_type,
            "amount": final_amount,   # ยอดเงินที่จะบันทึกจริง (0 ถ้าปิด)
            "reward_rate": final_rate # เรทที่จะบันทึกจริง (0 ถ้าปิด)
        })
        
        # บวกยอดรวมเฉพาะตัวที่ไม่ใช่เลขปิด
        total_amount += final_amount

    # =========================================================
    # 6. ตัดเงินและบันทึก
    # =========================================================
    user_db = db.query(User).filter(User.id == current_user.id).with_for_update().first()

    if user_db.credit_balance < total_amount:
        raise HTTPException(
            status_code=400, 
            detail=f"ยอดเงินไม่พอ (ขาด {total_amount - current_user.credit_balance:.2f} บาท)"
        )

    try:
        # ตัดเงิน
        user_db.credit_balance -= total_amount
        db.add(current_user)

        # สร้าง Header Ticket
        new_ticket = Ticket(
            shop_id=target_shop_id,
            user_id=current_user.id,
            lotto_type_id=ticket_in.lotto_type_id,
            round_date=target_round_date,
            note=ticket_in.note,
            total_amount=total_amount, # ยอดรวมนี้จะไม่รวมค่าเลขปิด
            status=TicketStatus.PENDING
        )
        db.add(new_ticket)
        db.flush() 

        # สร้าง Items
        for p_item in processed_items:
            t_item = TicketItem(
                ticket_id=new_ticket.id,
                number=p_item["number"],
                bet_type=p_item["bet_type"],
                amount=p_item["amount"],      # 0 ถ้าปิด
                reward_rate=p_item["reward_rate"], # 0 ถ้าปิด
                winning_amount=0,
                status=TicketStatus.PENDING
            )
            db.add(t_item)

        db.commit()
        db.refresh(new_ticket)
        return new_ticket

    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"ระบบขัดข้อง: {str(e)}")

# --- Stats & History ---
@router.get("/stats/range") 
def get_stats_range(
    start_date: str, 
    end_date: str,   
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        e_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    # ปรับเวลาให้ครอบคลุมทั้งวัน (UTC+7 workaround)
    start_utc = datetime.combine(s_date, time.min) - timedelta(hours=7)
    end_utc = datetime.combine(e_date, time.max) - timedelta(hours=7)

    # ✅ แก้ไข 1: base_filters เอาแค่ "เวลา" และ "ร้านค้า" พอ (อย่าเพิ่งกรองสถานะตรงนี้)
    base_filters = [
        Ticket.created_at >= start_utc,
        Ticket.created_at <= end_utc
    ]
    
    if current_user.role == UserRole.admin:
        base_filters.append(Ticket.shop_id == current_user.shop_id)

    # ---------------------------------------------------
    # 1. ยอดขาย (ต้องไม่รวมบิลยกเลิก)
    # ---------------------------------------------------
    sales_query = db.query(
        func.sum(Ticket.total_amount).label("total_sales"),
        func.count(Ticket.id).label("total_tickets"),
    ).filter(*base_filters, Ticket.status != TicketStatus.CANCELLED) # ✅ กรองไม่เอา Cancel ตรงนี้
    
    sales_result = sales_query.first()
    total_sales = sales_result.total_sales or 0
    total_tickets = sales_result.total_tickets or 0

    # ---------------------------------------------------
    # 2. ยอดจ่ายรางวัล (เฉพาะบิลที่ดี และถูกรางวัล)
    # ---------------------------------------------------
    payout_query = db.query(func.sum(TicketItem.winning_amount))\
        .join(Ticket)\
        .filter(*base_filters)\
        .filter(Ticket.status != TicketStatus.CANCELLED)\
        .filter(TicketItem.status == 'WIN')
        
    total_payout = payout_query.scalar() or 0

    # ---------------------------------------------------
    # 3. ยอดรอผล (Pending)
    # ---------------------------------------------------
    pending_query = db.query(func.sum(Ticket.total_amount))\
        .filter(*base_filters)\
        .filter(Ticket.status == TicketStatus.PENDING)
    
    total_pending = pending_query.scalar() or 0

    # ---------------------------------------------------
    # 4. ยอดบิลยกเลิก (Count)
    # ---------------------------------------------------
    # ✅ แก้ไข 2: query นี้จะทำงานได้แล้ว เพราะ base_filters ไม่ได้กัน Cancelled ออก
    cancelled_count = db.query(func.count(Ticket.id))\
        .filter(*base_filters, Ticket.status == TicketStatus.CANCELLED)\
        .scalar() or 0
    
    # คำนวณกำไร (ยอดขาย - จ่าย - รอผล)
    profit = total_sales - total_payout - total_pending

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_sales": total_sales,
        "total_tickets": total_tickets,
        "total_payout": total_payout,
        "total_pending": total_pending, 
        "total_cancelled": cancelled_count, # ✅ ค่านี้จะส่งออกไปถูกต้องแล้ว
        "profit": profit
    }

@router.get("/history", response_model=List[TicketResponse])
def read_history(
    skip: int = 0,
    limit: int = 30,
    lotto_type_id: Optional[UUID] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    target_start = None
    target_end = None

    try:
        if start_date and end_date:
            s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            e_date = datetime.strptime(end_date, "%Y-%m-%d").date()
            target_start = datetime.combine(s_date, time.min) - timedelta(hours=7)
            target_end = datetime.combine(e_date, time.max) - timedelta(hours=7)
        elif date:
            t_date = datetime.strptime(date, "%Y-%m-%d").date()
            target_start = datetime.combine(t_date, time.min) - timedelta(hours=7)
            target_end = datetime.combine(t_date, time.max) - timedelta(hours=7)
        else:
            today = (datetime.utcnow() + timedelta(hours=7)).date()
            target_start = datetime.combine(today, time.min) - timedelta(hours=7)
            target_end = datetime.combine(today, time.max) - timedelta(hours=7)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    query = db.query(Ticket).options(
        joinedload(Ticket.items),
        joinedload(Ticket.lotto_type)
    ).filter(
        Ticket.user_id == current_user.id,
        Ticket.created_at >= target_start,
        Ticket.created_at <= target_end
    )

    if lotto_type_id:
        query = query.filter(Ticket.lotto_type_id == lotto_type_id)

    tickets = query.order_by(Ticket.created_at.desc()).offset(skip).limit(limit).all()
    return tickets

@router.get("/shop_history", response_model=List[TicketResponse])
def get_shop_tickets(
    skip: int = 0,
    limit: int = 30,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if not current_user.shop_id:
         raise HTTPException(status_code=400, detail="No shop assigned")

    target_start = None
    target_end = None

    try:
        if start_date and end_date:
            s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            e_date = datetime.strptime(end_date, "%Y-%m-%d").date()
            target_start = datetime.combine(s_date, time.min) - timedelta(hours=7)
            target_end = datetime.combine(e_date, time.max) - timedelta(hours=7)
        elif date:
            t_date = datetime.strptime(date, "%Y-%m-%d").date()
            target_start = datetime.combine(t_date, time.min) - timedelta(hours=7)
            target_end = datetime.combine(t_date, time.max) - timedelta(hours=7)
        else:
            today = (datetime.utcnow() + timedelta(hours=7)).date()
            target_start = datetime.combine(today, time.min) - timedelta(hours=7)
            target_end = datetime.combine(today, time.max) - timedelta(hours=7)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    query = db.query(Ticket).options(
            joinedload(Ticket.user),
            joinedload(Ticket.lotto_type),
            joinedload(Ticket.items)
        ).filter(
            Ticket.shop_id == current_user.shop_id,
            Ticket.created_at >= target_start,
            Ticket.created_at <= target_end
        )

    if user_id:
        query = query.filter(Ticket.user_id == user_id)

    tickets = query.order_by(Ticket.created_at.desc())\
        .offset(skip)\
        .limit(limit)\
        .all()
        
    return tickets

@router.get("/stats/summary")
def get_summary_stats(
    period: str = "today",
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    today = date.today()
    filters = []

    if period == "today":
        filters.append(func.date(Ticket.created_at) == today)
    elif period == "yesterday":
        yesterday = today - timedelta(days=1)
        filters.append(func.date(Ticket.created_at) == yesterday)
    elif period == "this_month":
        filters.append(extract('month', Ticket.created_at) == today.month)
        filters.append(extract('year', Ticket.created_at) == today.year)

    filters.append(Ticket.status != TicketStatus.CANCELLED)

    if current_user.role == UserRole.admin:
        if not current_user.shop_id:
            raise HTTPException(status_code=400, detail="User has no shop")
        filters.append(Ticket.shop_id == current_user.shop_id)

    total_sales = db.query(func.sum(Ticket.total_amount)).filter(*filters).scalar() or 0

    payout_query = db.query(func.sum(TicketItem.winning_amount))\
        .join(Ticket)\
        .filter(*filters)\
        .filter(TicketItem.status == 'WIN')
        
    total_payout = payout_query.scalar() or 0

    return {
        "period": period,
        "total_sales": total_sales,
        "total_payout": total_payout,
        "profit": total_sales - total_payout
    }

@router.patch("/tickets/{ticket_id}/cancel")
def cancel_ticket(
    ticket_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    ticket = db.query(Ticket).options(joinedload(Ticket.user), joinedload(Ticket.lotto_type)).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if current_user.role == UserRole.member:
        if ticket.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not your ticket")
        
        if ticket.lotto_type.close_time:
            now_time = datetime.now().time()
            if now_time > ticket.lotto_type.close_time:
                raise HTTPException(status_code=400, detail="Cannot cancel: Market is closed")

    elif current_user.role == UserRole.admin:
        if ticket.shop_id != current_user.shop_id:
            raise HTTPException(status_code=403, detail="Cross-shop action denied")
    
    if ticket.status != TicketStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Cannot cancel ticket in {ticket.status} status")

    try:
        refund_amount = ticket.total_amount
        ticket.user.credit_balance += refund_amount
        
        actor = f"{current_user.username} ({current_user.role.value})"
        ticket.note = f"{ticket.note or ''} [Cancelled by {actor}]"
        
        ticket.status = TicketStatus.CANCELLED
        for item in ticket.items:
            item.status = TicketStatus.CANCELLED
            item.winning_amount = 0

        db.commit()

        return {"status": "success", "message": "Ticket cancelled", "refunded": refund_amount}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to cancel ticket")

@router.get("/stats/top_numbers")
def get_top_numbers(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    today = (datetime.utcnow() + timedelta(hours=7)).date()
    
    if start_date and end_date:
        try:
            s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            e_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            s_date = e_date = today
    else:
        s_date = e_date = today

    start_utc = datetime.combine(s_date, time.min) - timedelta(hours=7)
    end_utc = datetime.combine(e_date, time.max) - timedelta(hours=7)
    
    query = db.query(
        TicketItem.number,
        func.sum(TicketItem.amount).label("total_amount"),
        func.count(TicketItem.id).label("frequency")
    ).join(Ticket).filter(
        Ticket.created_at >= start_utc,
        Ticket.created_at <= end_utc,
        Ticket.status != 'CANCELLED'
    )

    if current_user.role == UserRole.admin:
        query = query.filter(Ticket.shop_id == current_user.shop_id)
        
    results = query.group_by(TicketItem.number)\
        .order_by(desc("total_amount"))\
        .limit(limit)\
        .all()
        
    return [
        {"number": r.number, "total_amount": r.total_amount, "frequency": r.frequency}
        for r in results
    ]

@router.get("/stats/members")
def get_member_stats(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        if start_date and end_date:
            s_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            e_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        else:
            s_date = e_date = (datetime.utcnow() + timedelta(hours=7)).date()
            
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    start_utc = datetime.combine(s_date, time.min) - timedelta(hours=7)
    end_utc = datetime.combine(e_date, time.max) - timedelta(hours=7)

    query = db.query(Ticket).options(
        joinedload(Ticket.user), 
        joinedload(Ticket.items)
    ).filter(
        Ticket.created_at >= start_utc,
        Ticket.created_at <= end_utc
    )

    if current_user.role == UserRole.admin:
        query = query.filter(Ticket.shop_id == current_user.shop_id)

    tickets = query.all()

    stats = {}
    for t in tickets:
        if not t.user: continue
        
        uid = str(t.user.id)
        if uid not in stats:
            stats[uid] = {
                "user_id": uid,
                "username": t.user.username,
                "full_name": t.user.full_name or "-",
                "role": t.user.role.value,
                "total_bet": Decimal(0),
                "total_win": Decimal(0),
                "pending_amount": Decimal(0),
                "cancelled_amount": Decimal(0),
                "bill_count": 0
            }
        
        s = stats[uid]
        s["bill_count"] += 1
        
        if t.status == TicketStatus.CANCELLED:
            s["cancelled_amount"] += t.total_amount
        else:
            s["total_bet"] += t.total_amount
            if t.status == TicketStatus.PENDING:
                s["pending_amount"] += t.total_amount
            elif t.status == TicketStatus.WIN:
                win_amt = sum(item.winning_amount for item in t.items if item.status == 'WIN')
                s["total_win"] += win_amt

    results = list(stats.values())
    results.sort(key=lambda x: x["total_bet"], reverse=True)

    return results

@router.delete("/rates/{profile_id}")
def delete_rate_profile(
    profile_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    profile = db.query(RateProfile).filter(RateProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Rate profile not found")

    linked_lottos = db.query(LottoType).filter(LottoType.rate_profile_id == profile_id).count()
    if linked_lottos > 0:
        raise HTTPException(
            status_code=400, 
            detail=f"ไม่สามารถลบได้ เนื่องจากมีหวย {linked_lottos} รายการใช้งานโปรไฟล์นี้อยู่"
        )

    db.delete(profile)
    db.commit()
    return {"status": "success", "message": "Deleted successfully"}

@router.get("/lottos/{lotto_id}", response_model=None)
def get_lotto_detail(
    lotto_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    lotto = db.query(LottoType).options(
        joinedload(LottoType.rate_profile),
        joinedload(LottoType.shop)
    ).filter(LottoType.id == lotto_id).first()

    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto not found")

    if current_user.role == UserRole.admin and lotto.shop_id != current_user.shop_id:
         if not lotto.is_template:
             raise HTTPException(status_code=403, detail="Access denied")
         
    rates = {}
    if lotto.rate_profile:
        rates = lotto.rate_profile.rates

    final_theme = "#2563EB"

    if lotto.shop and hasattr(lotto.shop, 'theme_color') and lotto.shop.theme_color:
        final_theme = lotto.shop.theme_color

    if lotto.category:
        category = db.query(LottoCategory).filter(
            func.cast(LottoCategory.id, String) == str(lotto.category)
        ).first()
        
        if category and category.color and category.color.startswith("#"):
            final_theme = category.color

    return {
        "id": lotto.id,
        "name": lotto.name,
        "img_url": lotto.img_url,
        "close_time": lotto.close_time,
        "rates": rates,
        "is_active": lotto.is_active,
        "theme_color": final_theme
    }