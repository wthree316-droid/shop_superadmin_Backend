from decimal import Decimal
from typing import List, Optional, Any, Dict
from datetime import datetime, time, date, timedelta
from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
from sqlalchemy import func, case, desc, extract
from pydantic import BaseModel
from app.core import lotto_cache

from app.api import deps
# Import Schemas
from app.schemas import (
    TicketCreate, TicketResponse, 
    LottoCreate, LottoResponse,
    RateProfileCreate, RateProfileResponse,
    NumberRiskCreate, NumberRiskResponse,
    BulkRateRequest, CategoryCreate, CategoryResponse
    # ลบ RewardHistoryResponse ออกเพราะไม่ได้ใช้ในไฟล์นี้
)
from app.db.session import get_db
from app.models.lotto import Ticket, TicketItem, LottoType, TicketStatus, RateProfile, NumberRisk, LottoCategory
from app.models.user import User, UserRole
from app.core import lotto_cache
from app.core.game_logic import expand_numbers
from app.core.audit_logger import write_audit_log
from app.core.risk_cache import get_cached_risks, invalidate_cache

from supabase import create_client, Client
from app.core.config import settings

router = APIRouter()


# เชื่อมต่อ Supabase
try:
    supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    BUCKET_NAME = "lotto_images"
except Exception as e:
    print(f"Supabase Init Error: {e}")

# --- APIs ---

# 1. API ดึง Rate Profile
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

@router.get("/categories", response_model=List[CategoryResponse])
def get_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # ดึงหมวดหมู่ของร้านตัวเอง + หมวดหมู่กลาง (shop_id=None)
    query = db.query(LottoCategory).filter(
        (LottoCategory.shop_id == current_user.shop_id) | (LottoCategory.shop_id == None)
    )
    return query.all()

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
        shop_id=current_user.shop_id
    )
    db.add(new_cat)
    db.commit()
    db.refresh(new_cat)
    return new_cat

# -------------------------------------------------------------------
# 1. ปรับแก้ API GET /lottos ให้ใช้ Cache
# -------------------------------------------------------------------
@router.get("/lottos", response_model=List[LottoResponse])
def get_lottos(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. กำหนดฟังก์ชันสำหรับดึงข้อมูลสด (ถ้า Cache ว่าง)
    def fetch_all_lottos():
        return db.query(LottoType).order_by(LottoType.id).all()

    # 2. เรียกข้อมูลจาก Cache (จะได้ List ของ Dict)
    all_lottos = lotto_cache.get_cached_lottos(fetch_all_lottos)

    # 3. กรองข้อมูล (Filter) ด้วย Python (เพราะข้อมูลอยู่ในแรมแล้ว เร็วมาก)
    filtered_lottos = []
    
    for lotto in all_lottos:
        # แปลง UUID ใน dict เป็น string เพื่อเทียบกับ current_user.shop_id (ที่เป็น UUID object)
        lotto_shop_id = str(lotto.get('shop_id')) if lotto.get('shop_id') else None
        user_shop_id = str(current_user.shop_id) if current_user.shop_id else None

        if current_user.role == UserRole.member:
            # สมาชิก: ต้อง Active + ไม่ใช่ Template + ตรงกับร้านตัวเอง
            if lotto.get('is_active') is True and lotto.get('is_template') is False:
                if user_shop_id:
                    if lotto_shop_id == user_shop_id:
                        filtered_lottos.append(lotto)
                else:
                    # ถ้าไม่มีสังกัดร้าน (กรณีระบบเปิด)
                    filtered_lottos.append(lotto)
                    
        elif current_user.role == UserRole.admin:
            # แอดมิน: ดูเฉพาะของร้านตัวเอง
            if lotto_shop_id == user_shop_id:
                filtered_lottos.append(lotto)
        
        else:
            # Superadmin: ดูได้หมด
            filtered_lottos.append(lotto)
            
    return filtered_lottos


# 1. API แก้ไขหมวดหมู่
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
    
    # ถ้าเป็น Admin ร้าน ห้ามแก้ของร้านอื่น (กรณีระบบ Multi-tenant)
    if current_user.role == UserRole.admin and category.shop_id != current_user.shop_id:
        raise HTTPException(status_code=403, detail="Access denied")

    category.label = cat_in.label
    category.color = cat_in.color
    
    db.commit()
    db.refresh(category)
    return category

# 2. API ลบหมวดหมู่
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

    if current_user.role == UserRole.admin and category.shop_id != current_user.shop_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # (Optional) เช็คก่อนว่ามีหวยใช้อยู่ไหม ถ้ามีห้ามลบ
    used_count = db.query(LottoType).filter(LottoType.category == str(cat_id)).count()
    if used_count > 0:
        raise HTTPException(status_code=400, detail=f"ไม่สามารถลบได้ มีหวย {used_count} รายการใช้งานอยู่")

    db.delete(category)
    db.commit()
    return {"status": "success", "message": "Category deleted"}

# Helper แปลงเวลา
def parse_time(t_str: str):
    if not t_str: return None
    try:
        if len(t_str) == 5: t_str += ":00"
        return datetime.strptime(t_str, "%H:%M:%S").time()
    except ValueError:
        return None
    
# [Create Lotto]
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
        raise HTTPException(status_code=400, detail=f"รหัสหวย {lotto_in.code} มีอยู่แล้วในร้านของคุณ")

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


# [Corrected Bulk Update]
@router.put("/lottos/bulk-rate-update")
def bulk_update_lotto_rates(
    body: BulkRateRequest, 
    db: Session = Depends(get_db),
    # ✅ เพิ่ม: ต้อง Login และเช็ค Role
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. Security Check: ต้องเป็น Admin หรือ Superadmin เท่านั้น
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        # 2. เริ่มสร้าง Query
        query = db.query(LottoType).filter(LottoType.is_template == False)

        # 3. Scope Check: ถ้าเป็น Admin ร้าน ต้องแก้ได้แค่หวยในร้านตัวเองเท่านั้น
        if current_user.role == UserRole.admin:
            # สำคัญมาก! ถ้าไม่ใส่บรรทัดนี้ ร้าน A จะไปแก้หวยร้าน B พังหมด
            query = query.filter(LottoType.shop_id == current_user.shop_id)

        # 4. Execute Update
        updated_count = query.update(
            {LottoType.rate_profile_id: body.rate_profile_id},
            synchronize_session=False
        )
        
        db.commit()
        return {"message": "Success", "updated_count": updated_count}

    except Exception as e:
        db.rollback()
        # print error เพื่อ debug
        print(f"Error bulk update: {e}") 
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดในการอัปเดตข้อมูล")

# [Update Lotto]
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

    # [Logic ลบรูปเก่า]
    if lotto_in.img_url and lotto.img_url and lotto_in.img_url != lotto.img_url:
        try:
            old_file_name = lotto.img_url.split("/")[-1]
            supabase.storage.from_(BUCKET_NAME).remove([old_file_name])
            print(f"🗑️ Deleted old image: {old_file_name}")
        except Exception as e:
            print(f"⚠️ Failed to delete old image: {e}")

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
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="ไม่สามารถลบหวยนี้ได้")
    
    return {"status": "success", "message": "Lotto deleted successfully"}

# ดึงรายการแม่แบบ
@router.get("/lottos/templates", response_model=List[LottoResponse])
def get_lotto_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    return db.query(LottoType).filter(LottoType.is_template == True).all()

# Import Default Lottos
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
@router.get("/risks/{lotto_id}", response_model=List[NumberRiskResponse])
def get_risks(
    lotto_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    return db.query(NumberRisk).filter(NumberRisk.lotto_type_id == lotto_id).all()

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

# --- Submit Ticket ---
@router.post("/submit_ticket", response_model=TicketResponse)
def submit_ticket(
    ticket_in: TicketCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. จัดการเรื่อง Shop ID (ใครเป็นคนส่งโพย)
    target_shop_id = current_user.shop_id
    if current_user.role == UserRole.superadmin:
        if ticket_in.shop_id:
            target_shop_id = ticket_in.shop_id
    elif current_user.role == UserRole.admin:
        target_shop_id = current_user.shop_id

    # 2. ตรวจสอบข้อมูลหวย และเวลาปิดรับ
    lotto = db.query(LottoType).filter(LottoType.id == ticket_in.lotto_type_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto type not found")
    
    now_time = datetime.now().time()
    if lotto.close_time and now_time > lotto.close_time:
        raise HTTPException(status_code=400, detail="หวยปิดรับแล้ว (Market Closed)")

    # 3. ตรวจสอบยอดเงินคงเหลือ
    total_amount = sum(item.amount for item in ticket_in.items)
    user_db = db.query(User).filter(User.id == current_user.id).with_for_update().first()

    if user_db.credit_balance < total_amount:
        raise HTTPException(
            status_code=400, 
            detail=f"ยอดเงินไม่พอ (ขาด {total_amount - current_user.credit_balance:.2f} บาท)"
        )

    try:
        # ตัดเงิน และสร้าง Header ของ Ticket
        user_db.credit_balance -= total_amount
        db.add(current_user)

        new_ticket = Ticket(
            shop_id=target_shop_id,
            user_id=current_user.id,
            lotto_type_id=ticket_in.lotto_type_id,
            note=ticket_in.note,
            total_amount=total_amount,
            status=TicketStatus.PENDING
        )
        db.add(new_ticket)
        db.flush() # flush เพื่อให้ new_ticket.id ถูกสร้าง

        # -----------------------------------------------------------
        # 🔥 จุดแก้ไขสำคัญ: สร้าง Risk Lookup Map แบบละเอียด
        # -----------------------------------------------------------
        # ดึงข้อมูล Risk ทั้งหมดของหวยนี้ (แนะนำให้ดึงสดจาก DB ก่อนเพื่อความชัวร์เรื่อง Type)
        # (ถ้าจะใช้ Cache ต้องแก้ไฟล์ risk_cache.py ให้เก็บ structure ใหม่ก่อน)
        risk_entries = db.query(NumberRisk).filter(NumberRisk.lotto_type_id == ticket_in.lotto_type_id).all()
        
        # สร้าง Dictionary เพื่อการค้นหาที่รวดเร็ว
        # Key จะหน้าตาแบบนี้: "12:2up" หรือ "12:ALL"
        risk_lookup = {}
        for r in risk_entries:
            key = f"{r.number}:{r.specific_bet_type}" # เช่น "59:2up"
            risk_lookup[key] = r.risk_type

        # ดึง Rate Profile มาเตรียมไว้
        rates = {}
        if lotto.rate_profile:
            rates = lotto.rate_profile.rates 
        
        # 4. วนลูปสร้างรายการย่อย (Items)
        for item_in in ticket_in.items:
            expanded_numbers = expand_numbers(item_in.number, item_in.bet_type)
            if not expanded_numbers:
                raise HTTPException(status_code=400, detail=f"รูปแบบตัวเลขไม่ถูกต้อง: {item_in.number}")

            # ดึงการตั้งค่าเรท (Min/Max/Pay)
            rate_config = rates.get(item_in.bet_type, {})
            if isinstance(rate_config, (int, float, str, Decimal)):
                pay_rate = Decimal(str(rate_config))
                min_bet = Decimal("1")
                max_bet = Decimal("100000")
            else:
                pay_rate = Decimal(str(rate_config.get('pay', 0)))
                min_bet = Decimal(str(rate_config.get('min', 1)))
                max_bet = Decimal(str(rate_config.get('max', 0)))

            if pay_rate == 0:
                 raise HTTPException(status_code=400, detail=f"ไม่พบอัตราจ่ายสำหรับประเภท: {item_in.bet_type}")

            if item_in.amount < min_bet:
                raise HTTPException(status_code=400, detail=f"แทงขั้นต่ำ {min_bet:,.0f} บาท ({item_in.bet_type})")
            
            if max_bet > 0 and item_in.amount > max_bet:
                raise HTTPException(status_code=400, detail=f"แทงสูงสุด {max_bet:,.0f} บาท ({item_in.bet_type})")

            # 5. ตรวจสอบเลขแต่ละตัว (Expanded Numbers)
            for num in expanded_numbers:
                final_rate = pay_rate
                risk_status = None

                # 🔥 ตรวจสอบความเสี่ยง (Logic ใหม่)
                # 1. เช็คแบบเจาะจงประเภทก่อน (เช่น 12 ประเภท 2up)
                specific_key = f"{num}:{item_in.bet_type}"
                if specific_key in risk_lookup:
                    risk_status = risk_lookup[specific_key]
                
                # 2. ถ้าไม่เจอเจาะจง ให้เช็คแบบเหมาหมด (ALL)
                else:
                    general_key = f"{num}:ALL"
                    if general_key in risk_lookup:
                        risk_status = risk_lookup[general_key]

                # ดำเนินการตามสถานะที่เจอ
                if risk_status == "CLOSE":
                    # แปลงชื่อประเภทเป็นภาษาไทยให้ดูง่ายตอนแจ้งเตือน
                    type_th = {
                        '2up': '2ตัวบน', '2down': '2ตัวล่าง', 
                        '3top': '3ตัวบน', '3tod': '3ตัวโต๊ด',
                        'run_up': 'วิ่งบน', 'run_down': 'วิ่งล่าง'
                    }.get(item_in.bet_type, item_in.bet_type)
                    
                    raise HTTPException(status_code=400, detail=f"เลข {num} ({type_th}) ปิดรับแล้ว")
                
                elif risk_status == "HALF":
                    final_rate = pay_rate / 2

                # บันทึกลง DB
                t_item = TicketItem(
                    ticket_id=new_ticket.id,
                    number=num,
                    bet_type=item_in.bet_type,
                    amount=item_in.amount,
                    reward_rate=final_rate,
                    winning_amount=0,
                    status=TicketStatus.PENDING
                )
                db.add(t_item)

        db.commit()
        db.refresh(new_ticket)
        return new_ticket

    except Exception as e:
        db.rollback()
        print(f"Error submit ticket: {e}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"ระบบขัดข้อง: {str(e)}")

# --- Stats & History ---
@router.get("/stats/daily")  # <-- 1. เปลี่ยนชื่อ endpoint ให้ general ขึ้น
def get_daily_stats(
    date_str: Optional[str] = None, # <-- 2. รับวันที่เข้ามา (Format: YYYY-MM-DD)
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # --- ส่วนจัดการวันที่ ---
    if date_str:
        # กรณีมีการเลือกวันที่มา: แปลง String เป็น Date Object
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        # กรณีไม่เลือก: ใช้วันปัจจุบัน (UTC+7)
        target_date = (datetime.utcnow() + timedelta(hours=7)).date()

    # สร้างช่วงเวลา Start - End ของวันนั้น (เพื่อประสิทธิภาพ Query)
    # เช่น 2023-10-25 00:00:00 ถึง 2023-10-25 23:59:59
    start_of_day_thai = datetime.combine(target_date, time.min) # 00:00
    end_of_day_thai = datetime.combine(target_date, time.max)   # 23:59

    # 2. ลบ 7 ชั่วโมงเพื่อแปลงกลับเป็น UTC (สำหรับ Database)
    start_utc = start_of_day_thai - timedelta(hours=7)
    end_utc = end_of_day_thai - timedelta(hours=7)
    # --- 3. Query ยอดขาย (ใช้ Range Filter เร็วกว่า func.date) ---
    query = db.query(
        func.sum(Ticket.total_amount).label("total_sales"),
        func.count(Ticket.id).label("total_tickets"),
    ).filter(
        Ticket.created_at >= start_utc,  # มากกว่าหรือเท่ากับ 00:00
        Ticket.created_at <= end_utc,    # น้อยกว่าหรือเท่ากับ 23:59
        Ticket.status != TicketStatus.CANCELLED
    )
    
    # กรองร้านค้า (ถ้าเป็น Admin ร้าน)
    if current_user.role == UserRole.admin:
        query = query.filter(Ticket.shop_id == current_user.shop_id)

    sales_result = query.first()
    total_sales = sales_result.total_sales or 0
    total_tickets = sales_result.total_tickets or 0

    # --- 4. Query ยอดจ่ายรางวัล ---
    payout_query = db.query(func.sum(TicketItem.winning_amount))\
        .join(Ticket)\
        .filter(Ticket.created_at >= start_utc)\
        .filter(Ticket.created_at <= end_utc)\
        .filter(TicketItem.status == 'WIN')\
        .filter(Ticket.status != TicketStatus.CANCELLED)
        
    if current_user.role == UserRole.admin:
        payout_query = payout_query.filter(Ticket.shop_id == current_user.shop_id)
        
    total_payout = payout_query.scalar() or 0

    return {
        "date": target_date, # ส่งกลับไปด้วยว่านี่คือข้อมูลของวันไหน
        "total_sales": total_sales,
        "total_tickets": total_tickets,
        "total_payout": total_payout,
        "profit": total_sales - total_payout
    }

@router.get("/history", response_model=List[TicketResponse])
def read_history(
    skip: int = 0,
    limit: int = 100,
    lotto_type_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    query = db.query(Ticket).options(
        joinedload(Ticket.items),
        joinedload(Ticket.lotto_type)
    ).filter(Ticket.user_id == current_user.id)

    if lotto_type_id:
        query = query.filter(Ticket.lotto_type_id == lotto_type_id)

    tickets = query.order_by(Ticket.created_at.desc()).offset(skip).limit(limit).all()
    return tickets

@router.get("/shop_history", response_model=List[TicketResponse])
def get_shop_tickets(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if not current_user.shop_id:
         raise HTTPException(status_code=400, detail="No shop assigned")

    tickets = (
        db.query(Ticket)
        .options(
            joinedload(Ticket.user),
            joinedload(Ticket.lotto_type)      
        )
        .filter(Ticket.shop_id == current_user.shop_id)
        .order_by(Ticket.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return tickets

@router.get("/stats/summary")
def get_summary_stats(
    period: str = "today", # today, yesterday, this_month
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. Security Check
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. เตรียมตัวแปรวันที่
    today = date.today()
    filters = []

    # 3. Logic การ Filter ตามช่วงเวลา
    if period == "today":
        # เฉพาะวันนี้
        filters.append(func.date(Ticket.created_at) == today)
        
    elif period == "yesterday":
        # เฉพาะเมื่อวาน
        yesterday = today - timedelta(days=1) # ต้อง import timedelta ข้างบนด้วย
        filters.append(func.date(Ticket.created_at) == yesterday)
        
    elif period == "this_month":
        # ทั้งเดือนนี้
        filters.append(extract('month', Ticket.created_at) == today.month)
        filters.append(extract('year', Ticket.created_at) == today.year)

    # 4. Filter เพิ่มเติม: ไม่เอาบิลที่ยกเลิก
    filters.append(Ticket.status != TicketStatus.CANCELLED)

    # 5. Filter ร้านค้า (ถ้าเป็น Admin ร้าน ดูได้แค่ร้านตัวเอง)
    if current_user.role == UserRole.admin:
        if not current_user.shop_id:
            raise HTTPException(status_code=400, detail="User has no shop")
        filters.append(Ticket.shop_id == current_user.shop_id)

    # --- Query 1: ยอดขายรวม (Total Sales) ---
    # รวมเงินจาก Ticket.total_amount
    total_sales = db.query(func.sum(Ticket.total_amount)).filter(*filters).scalar() or 0

    # --- Query 2: ยอดจ่ายรางวัล (Total Payout) ---
    # ต้อง Join ไปที่ TicketItem เพื่อดูว่าตัวไหนถูกรางวัล (status='WIN')
    # และต้องกรอง Ticket ตาม filters ด้านบนด้วย
    payout_query = db.query(func.sum(TicketItem.winning_amount))\
        .join(Ticket)\
        .filter(*filters)\
        .filter(TicketItem.status == 'WIN') # เฉพาะรายการที่ถูก
        
    total_payout = payout_query.scalar() or 0

    # 6. ส่งผลลัพธ์กลับ
    return {
        "period": period,
        "total_sales": total_sales,     # ยอดขาย
        "total_payout": total_payout,   # ยอดจ่ายจริง
        "profit": total_sales - total_payout # กำไร (ขาดทุนถ้าติดลบ)
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

        background_tasks.add_task(
            write_audit_log,
            user=current_user,
            action="CANCEL_TICKET",
            target_table="tickets",
            target_id=str(ticket.id),
            details={
                "refund_amount": float(refund_amount),
                "reason": "User requested cancel" if current_user.role == UserRole.member else "Admin force cancel"
            },
            request=request
        )

        return {"status": "success", "message": "Ticket cancelled", "refunded": refund_amount}

    except Exception as e:
        db.rollback()
        print(f"Cancel Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to cancel ticket")

@router.get("/stats/top_numbers")
def get_top_numbers(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    today = date.today()
    
    query = db.query(
        TicketItem.number,
        func.sum(TicketItem.amount).label("total_amount"),
        func.count(TicketItem.id).label("frequency")
    ).join(Ticket).filter(
        func.date(Ticket.created_at) == today,
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
    date_str: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 1. จัดการวันที่ (Timezone Safe)
    if date_str:
        try:
            target_date_thai = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date_thai = (datetime.utcnow() + timedelta(hours=7)).date()
    else:
        target_date_thai = (datetime.utcnow() + timedelta(hours=7)).date()

    # 2. แปลงเป็นช่วงเวลา UTC
    start_utc = datetime.combine(target_date_thai, time.min) - timedelta(hours=7)
    end_utc = datetime.combine(target_date_thai, time.max) - timedelta(hours=7)

    # 3. Query (ไม่กรอง Role เพื่อให้เห็น Admin เล่นด้วย)
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

    # 4. วนลูปสรุปยอด
    stats = {}
    for t in tickets:
        if not t.user: continue
        
        uid = str(t.user.id)
        if uid not in stats:
            stats[uid] = {
                "user_id": uid,
                "username": t.user.username,
                "full_name": t.user.full_name or "-",
                "role": t.user.role.value, # เพิ่ม Role ให้รู้
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

# ดึงรายละเอียดหวย 1 รายการ
# [Note] ถ้าต้องการ Type Strict แนะนำให้เพิ่ม LottoDetailResponse ใน schemas.py
# แต่ตอนนี้ใช้ response_model=None (คืนค่า Dict) ไปก่อน เพื่อไม่ต้องแก้ไฟล์อื่น
@router.get("/lottos/{lotto_id}", response_model=None)
def get_lotto_detail(
    lotto_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    lotto = db.query(LottoType).options(joinedload(LottoType.rate_profile)).filter(LottoType.id == lotto_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto not found")

    if current_user.role == UserRole.admin and lotto.shop_id != current_user.shop_id:
         if not lotto.is_template:
             raise HTTPException(status_code=403, detail="Access denied")
         
    rates = {}
    if lotto.rate_profile:
        rates = lotto.rate_profile.rates

    return {
        "id": lotto.id,
        "name": lotto.name,
        "img_url": lotto.img_url,
        "close_time": lotto.close_time,
        "rates": rates,
        "is_active": lotto.is_active
    }
