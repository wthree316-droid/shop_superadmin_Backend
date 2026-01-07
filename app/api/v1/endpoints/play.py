from decimal import Decimal
from typing import List, Optional, Any
from datetime import datetime, time, date
from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
from sqlalchemy import func, case, desc
from pydantic import BaseModel

from app.api import deps
# Import Schemas ที่เราเพิ่งรวมไฟล์มา
from app.schemas import (
    TicketCreate, TicketResponse, 
    LottoCreate, LottoResponse,  # <--- ใช้ชื่อมาตรฐานนี้แทน Full
    RateProfileCreate, RateProfileResponse,
    NumberRiskCreate, NumberRiskResponse,
    RewardHistoryResponse
)
from app.db.session import get_db
from app.models.lotto import Ticket, TicketItem, LottoType, TicketStatus, RateProfile, NumberRisk
from app.models.user import User, UserRole
from app.core.game_logic import expand_numbers
from app.core.audit_logger import write_audit_log
from app.core.risk_cache import get_cached_risks, invalidate_cache # [เพิ่ม]

from supabase import create_client, Client
from app.core.config import settings

router = APIRouter()

# [เพิ่ม] เชื่อมต่อ Supabase เพื่อใช้คำสั่งลบ
try:
    supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    BUCKET_NAME = "lotto_images"
except Exception as e:
    print(f"Supabase Init Error: {e}")

@router.get("/rates", response_model=List[RateProfileResponse])
def get_rate_profiles(db: Session = Depends(get_db)):
    return db.query(RateProfile).all()

@router.post("/rates", response_model=RateProfileResponse)
def create_rate_profile(
    profile_in: RateProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    new_profile = RateProfile(name=profile_in.name, rates=profile_in.rates)
    db.add(new_profile)
    db.commit()
    db.refresh(new_profile)
    return new_profile

# --- APIs ---

@router.get("/lottos", response_model=List[LottoResponse])
def get_lottos(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    query = db.query(LottoType)
    
    # ถ้าเป็น SuperAdmin ให้เห็นหมด หรือเห็นเฉพาะ Template ก็ได้แล้วแต่ตกลง
    # แต่ปกติหน้านี้คือหน้า "เล่นหวย" ของลูกค้า หรือหน้า "จัดการหวย" ของร้าน
    
    if current_user.role == UserRole.member:
        # ลูกค้า: เห็นเฉพาะที่ Active และเป็นของร้านที่ตัวเองสังกัด (ถ้ามี)
        # หรือถ้าเป็นเว็บรวม ก็เห็นทั้งหมดที่เป็น shop_id ของเว็บหลัก
        query = query.filter(LottoType.is_active == True, LottoType.is_template == False)
        if current_user.shop_id:
             query = query.filter(LottoType.shop_id == current_user.shop_id)

    elif current_user.role == UserRole.admin:
        # แอดมินร้าน: เห็นเฉพาะของร้านตัวเอง
        query = query.filter(LottoType.shop_id == current_user.shop_id)
        
    # ถ้า SuperAdmin อาจจะอยากเห็นทั้งหมด หรือต้องมี API แยก
    
    return query.order_by(LottoType.id).all()

# Helper แปลงเวลา
def parse_time(t_str: str):
    if not t_str: return None
    try:
        if len(t_str) == 5: t_str += ":00"
        return datetime.strptime(t_str, "%H:%M:%S").time()
    except ValueError:
        return None
    
# [Create Lotto] ใช้ Schema มาตรฐาน
@router.post("/lottos", response_model=LottoResponse)
def create_lotto(
    lotto_in: LottoCreate, 
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. อนุญาตทั้ง Admin ร้าน และ SuperAdmin
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. กำหนดค่า shop_id และ is_template
    # ถ้าส่งมาว่า is_template=True (จากหน้า SuperAdmin) -> เป็นแม่แบบ, shop_id=None
    # ถ้าเป็น Admin ร้านสร้างเอง -> shop_id=current_user.shop_id, is_template=False
    
    is_template = getattr(lotto_in, "is_template", False) # รับค่าจาก Frontend ถ้ามี
    shop_id = None
    
    if current_user.role == UserRole.admin:
        shop_id = current_user.shop_id
        is_template = False # Admin ร้านห้ามสร้าง Template

    if db.query(LottoType).filter(LottoType.code == lotto_in.code).first():
        raise HTTPException(status_code=400, detail="Code already exists")

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
    db.refresh(new_lotto)
    return new_lotto

# [Update Lotto] ใช้ Schema มาตรฐาน
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
    # ถ้ามีการส่ง img_url มาใหม่ และ ไม่ตรงกับอันเดิม และอันเดิมมีค่าอยู่
    if lotto_in.img_url and lotto.img_url and lotto_in.img_url != lotto.img_url:
        try:
            # ดึงชื่อไฟล์จาก URL (เช่น https://.../lotto_images/uuid.jpg -> uuid.jpg)
            old_file_name = lotto.img_url.split("/")[-1]
            
            # สั่งลบใน Supabase
            supabase.storage.from_(BUCKET_NAME).remove([old_file_name])
            print(f"🗑️ Deleted old image: {old_file_name}")
        except Exception as e:
            print(f"⚠️ Failed to delete old image: {e}")

    # อัปเดตข้อมูลตามปกติ
    lotto.name = lotto_in.name
    lotto.code = lotto_in.code
    lotto.category = lotto_in.category
    lotto.rate_profile_id = lotto_in.rate_profile_id
    
    lotto.img_url = lotto_in.img_url # บรรทัดนี้จะเซฟ URL ใหม่ทับ
    lotto.api_link = lotto_in.api_link
    lotto.open_days = lotto_in.open_days
    
    lotto.open_time = parse_time(lotto_in.open_time)
    lotto.close_time = parse_time(lotto_in.close_time)
    lotto.result_time = parse_time(lotto_in.result_time)
    
    db.commit()
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
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="ไม่สามารถลบหวยนี้ได้")
    
    return {"status": "success", "message": "Lotto deleted successfully"}

# ดึงรายการแม่แบบ (สำหรับให้ร้านค้าเลือกดู หรือ SuperAdmin จัดการ)
@router.get("/lottos/templates", response_model=List[LottoResponse])
def get_lotto_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # ดึงเฉพาะที่เป็น Template
    return db.query(LottoType).filter(LottoType.is_template == True).all()

# ฟังก์ชันดึงข้อมูลจากแม่แบบ มาใส่ร้านตัวเอง (Clone)
@router.post("/lottos/import_defaults")
def import_default_lottos(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. เช็คสิทธิ์: ต้องเป็นแอดมินร้านเท่านั้น
    if current_user.role != UserRole.admin or not current_user.shop_id:
        raise HTTPException(status_code=403, detail="Only Shop Admin can import")

    # 2. ดึงแม่แบบทั้งหมดจาก Super Admin (is_template = True)
    templates = db.query(LottoType).filter(LottoType.is_template == True).all()
    if not templates:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลแม่แบบจากระบบกลาง (Super Admin ยังไม่ได้สร้าง)")

    # 3. ต้องมี Rate Profile ของร้านอย่างน้อย 1 อันเพื่อเอามาผูก
    # (สมมติว่าร้านสร้าง Rate Profile ไว้แล้ว เราจะเอาอันแรกมาใช้)
    # *หมายเหตุ: ในอนาคตคุณอาจต้องเพิ่ม shop_id ใน RateProfile เพื่อความชัวร์
    default_rate = db.query(RateProfile).first() 
    
    if not default_rate:
         raise HTTPException(status_code=400, detail="กรุณาสร้าง 'เรทราคา' ในร้านค้าก่อนกดดึงข้อมูล")

    imported_count = 0
    for tmpl in templates:
        # 4. เช็คว่าร้านเรามีหวย code นี้หรือยัง (กันซ้ำ)
        exists = db.query(LottoType).filter(
            LottoType.shop_id == current_user.shop_id,
            LottoType.code == tmpl.code
        ).first()
        
        if not exists:
            # 5. Clone ข้อมูลจากแม่แบบ มาเป็นของร้าน
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
                
                is_active=True,         # เปิดใช้งานทันที
                is_template=False,      # ของร้าน ไม่ใช่แม่แบบ
                shop_id=current_user.shop_id, # ผูกกับร้านเรา
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
        NumberRisk.number == risk_in.number
    ).first()

    if existing:
        existing.risk_type = risk_in.risk_type
        db.commit()
        db.refresh(existing)
        return existing

    new_risk = NumberRisk(
        lotto_type_id=risk_in.lotto_type_id,
        number=risk_in.number,
        risk_type=risk_in.risk_type
    )
    db.add(new_risk)
    db.commit()
    db.refresh(new_risk)
    # [เพิ่ม] ล้าง Cache ทันที เพื่อให้ User เห็นผลการอั้นเลขทันที
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

# --- [ไฮไลท์] Submit Ticket แบบตัดเงินจริง ---
@router.post("/submit_ticket", response_model=TicketResponse)
def submit_ticket(
    ticket_in: TicketCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. ตรวจสอบสิทธิ์ร้านค้า (สำหรับ Admin/Superadmin ที่คีย์แทนลูกค้า)
    target_shop_id = current_user.shop_id
    if current_user.role == UserRole.superadmin:
        if ticket_in.shop_id:
            target_shop_id = ticket_in.shop_id
    elif current_user.role == UserRole.admin:
        # Admin ร้านตัวเอง คีย์ให้ลูกค้าในร้านตัวเองได้ (แต่ตอนนี้เราใช้ user_id ของคนคีย์ไปก่อน)
        target_shop_id = current_user.shop_id

    # 2. ตรวจสอบหวย (Lotto Type)
    lotto = db.query(LottoType).filter(LottoType.id == ticket_in.lotto_type_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto type not found")
    
    # (Optional) เช็คเวลาปิดรับ
    now_time = datetime.now().time()
    if lotto.close_time and now_time > lotto.close_time:
        raise HTTPException(status_code=400, detail="Lotto is closed")

    # 3. คำนวณยอดเงินรวม
    total_amount = sum(item.amount for item in ticket_in.items)
    
    # 4. ตรวจสอบเงินในกระเป๋า (สำคัญมาก!)
    if current_user.credit_balance < total_amount:
        raise HTTPException(
            status_code=400, 
            detail=f"ยอดเงินไม่พอ (ขาด {total_amount - current_user.credit_balance:.2f} บาท)"
        )

    try:
        # --- เริ่ม Transaction ---
        
        # 5. ตัดเงินลูกค้า
        current_user.credit_balance -= total_amount
        db.add(current_user)

        # 6. สร้าง Ticket Header
        new_ticket = Ticket(
            shop_id=target_shop_id,
            user_id=current_user.id,
            lotto_type_id=ticket_in.lotto_type_id,
            note=ticket_in.note,
            total_amount=total_amount,
            status=TicketStatus.PENDING
        )
        db.add(new_ticket)
        db.flush() # flush เพื่อให้ได้ new_ticket.id มาใช้ก่อน commit

        # ดึงข้อมูล Risk ทั้งหมดของหวยนี้มาเตรียมไว้ใน Memory (เพื่อลด Query)
        def fetch_risks_from_db(lotto_id_str):
            return db.query(NumberRisk).filter(NumberRisk.lotto_type_id == lotto_id_str).all()

        # ดึง Risk Map จาก Cache (เร็วมาก O(1))
        risk_map = get_cached_risks(str(ticket_in.lotto_type_id), fetch_risks_from_db)


        # 7. สร้าง Ticket Items (รายการย่อย)
        # ดึงเรทจาก Relationship rate_profile
        # 7. สร้าง Ticket Items
        rates = {}
        if lotto.rate_profile:
            rates = lotto.rate_profile.rates # ตอนนี้ rates เป็น Dict ซ้อน Dict
        
        for item_in in ticket_in.items:
            # 7.1 แตกตัวเลข
            expanded_numbers = expand_numbers(item_in.number, item_in.bet_type)
            if not expanded_numbers:
                raise HTTPException(status_code=400, detail=f"Invalid number: {item_in.number}")

            # 7.2 [แก้ใหม่] ดึง Config ของประเภทนี้ (รองรับทั้งแบบเก่าและใหม่)
            rate_config = rates.get(item_in.bet_type, {})
            
            # แปลงค่าให้เป็นมาตรฐาน
            if isinstance(rate_config, (int, float, str, Decimal)):
                # กรณีข้อมูลเก่า (มีแค่ราคา)
                pay_rate = Decimal(str(rate_config))
                min_bet = Decimal("1")
                max_bet = Decimal("100000") # ค่า Default สูงๆ
            else:
                # กรณีข้อมูลใหม่ (มีครบ)
                pay_rate = Decimal(str(rate_config.get('pay', 0)))
                min_bet = Decimal(str(rate_config.get('min', 1)))
                max_bet = Decimal(str(rate_config.get('max', 0))) # 0 หรือ null อาจแปลว่าไม่อั้น

            # Fallback ถ้าหาเรทไม่เจอ
            if pay_rate == 0:
                 if "2" in item_in.bet_type: pay_rate = Decimal("90")
                 elif "3" in item_in.bet_type: pay_rate = Decimal("900")

            # 7.3 [เพิ่ม] ตรวจสอบ Min/Max Bet
            if item_in.amount < min_bet:
                raise HTTPException(
                    status_code=400, 
                    detail=f"แทงขั้นต่ำ {min_bet:,.0f} บาท (สำหรับ {item_in.bet_type})"
                )
            
            if max_bet > 0 and item_in.amount > max_bet:
                raise HTTPException(
                    status_code=400, 
                    detail=f"แทงสูงสุดไม่เกิน {max_bet:,.0f} บาท (สำหรับ {item_in.bet_type})"
                )
            # 7.4 [เพิ่มใหม่] ตรวจสอบ Risk ทีละเลข
            for num in expanded_numbers:
                # Default status
                final_rate = pay_rate
                # เช็คว่าเลขนี้ติด Blacklist ไหม
                if num in risk_map:
                    if risk_map[num] == "CLOSE":
                        raise HTTPException(status_code=400, detail=f"เลข {num} ปิดรับแล้ว")
                    elif risk_map[num] == "HALF":
                        final_rate = pay_rate / 2

                t_item = TicketItem(
                    ticket_id=new_ticket.id,
                    number=num,
                    bet_type=item_in.bet_type,
                    amount=item_in.amount,
                    reward_rate=final_rate, # <--- ใช้เรทที่ผ่านการคำนวณแล้ว
                    winning_amount=0,
                    status=TicketStatus.PENDING
                )
                db.add(t_item)

        # 8. ยืนยันข้อมูลทั้งหมดลง DB
        db.commit()
        db.refresh(new_ticket)
        return new_ticket

    except Exception as e:
        db.rollback() # ถ้ามี Error อะไรก็ตาม ให้คืนเงินลูกค้าและยกเลิกบิล
        print(f"Error submit ticket: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

# --- API Dashboard Stats ---
@router.get("/stats/today")
def get_daily_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    today = date.today()
    
    # Base Query
    query = db.query(
        # 1. ยอดขายรวม (Sum total_amount)
        func.sum(Ticket.total_amount).label("total_sales"),
        
        # 2. จำนวนบิล
        func.count(Ticket.id).label("total_tickets"),
        
        # 3. ยอดจ่ายจริง (Sum winning_amount ของ TicketItem)
        # ต้อง Join ไปที่ TicketItem เพื่อหาว่าตัวไหนถูกรางวัล
        # แต่วิธีง่ายกว่าคือ Query แยก หรือถ้า Ticket มี field winning_amount จะเร็วมาก
        # ในที่นี้เราจะ Query แยกเพื่อความชัวร์และไม่งง
    ).filter(func.date(Ticket.created_at) == today)
    
    # Filter ร้านใครร้านมัน
    if current_user.role == UserRole.admin:
        query = query.filter(Ticket.shop_id == current_user.shop_id)

    # ก้อนที่ 1: ยอดขาย & จำนวนบิล
    sales_result = query.first()
    total_sales = sales_result.total_sales or 0
    total_tickets = sales_result.total_tickets or 0

    # ก้อนที่ 2: ยอดจ่าย (Payout) - ต้องไปดึงจาก TicketItem ที่สถานะ WIN
    # เราจะ join Ticket -> TicketItem
    payout_query = db.query(func.sum(TicketItem.winning_amount))\
        .join(Ticket)\
        .filter(func.date(Ticket.created_at) == today)\
        .filter(TicketItem.status == 'WIN') # เฉพาะที่ถูกรางวัล
        
    if current_user.role == UserRole.admin:
        payout_query = payout_query.filter(Ticket.shop_id == current_user.shop_id)
        
    total_payout = payout_query.scalar() or 0

    return {
        "date": today,
        "total_sales": total_sales,
        "total_tickets": total_tickets,
        "total_payout": total_payout,
        "profit": total_sales - total_payout
    }


# ดูประวัติการแทง
@router.get("/history", response_model=List[TicketResponse])
def get_my_tickets(
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Query Ticket ของ User คนนี้ เรียงจากใหม่ไปเก่า
    tickets = (
        db.query(Ticket)
        .options(
            joinedload(Ticket.user),
            joinedload(Ticket.items),
            joinedload(Ticket.lotto_type)
        )
        .filter(Ticket.user_id == current_user.id)
        .order_by(Ticket.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return tickets
# ดูประวัติของร้าน
@router.get("/shop_history", response_model=List[TicketResponse])
def get_shop_tickets(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # เช็คว่า user มีร้านไหม
    if not current_user.shop_id:
         raise HTTPException(status_code=400, detail="No shop assigned")

    # ดึง Ticket ทั้งหมดที่เป็นของร้านนี้ (ไม่สนว่าใครซื้อ)
    tickets = (
        db.query(Ticket)
        .options(
            joinedload(Ticket.user),
            # joinedload(Ticket.items),  คอมเม้นไว้เพราะ item มีจำนวนเยอะถ้าดึงทุกขั้นจะเปลือง
            joinedload(Ticket.lotto_type)      
        )
        .filter(Ticket.shop_id == current_user.shop_id)
        .order_by(Ticket.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return tickets

# ตรวจผลรางวัลของเมมเบ้อ
@router.get("/stats/summary")
def get_summary_stats(
    period: str = "today", # today, yesterday, this_month
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Logic การ Filter วันที่
    # ... (Query Sum ยอดขาย / ยอดถูกรางวัล) ...
    # Return { total_sales: 10000, total_payout: 5000, profit: 5000 }
    pass


# [เพิ่ม] API ยกเลิกโพย (Cancel Ticket)
@router.patch("/tickets/{ticket_id}/cancel")
def cancel_ticket(
    ticket_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. หา Ticket ก่อน
    ticket = db.query(Ticket).options(joinedload(Ticket.user), joinedload(Ticket.lotto_type)).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # 2. Logic แยกตาม Role
    if current_user.role == UserRole.member:
        # 2.1 ต้องเป็นของตัวเองเท่านั้น
        if ticket.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not your ticket")
        
        # 2.2 หวยต้องยังไม่ปิด (สำคัญ!)
        # สมมติ ticket.lotto_type.close_time เป็น time object (เช่น 15:30:00)
        if ticket.lotto_type.close_time:
            now_time = datetime.now().time()
            # ถ้าเลยเวลาปิดแล้ว ห้ามยกเลิก
            if now_time > ticket.lotto_type.close_time:
                raise HTTPException(status_code=400, detail="Cannot cancel: Market is closed")

    elif current_user.role == UserRole.admin:
        # Admin ยกเลิกได้เฉพาะร้านตัวเอง
        if ticket.shop_id != current_user.shop_id:
            raise HTTPException(status_code=403, detail="Cross-shop action denied")
    
    # Superadmin ยกเลิกได้หมด (ผ่านไปเลย)

    # 3. เช็คสถานะ (ต้อง PENDING เท่านั้น)
    if ticket.status != TicketStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Cannot cancel ticket in {ticket.status} status")

    try:
        # 4. คืนเงิน (Refund)
        refund_amount = ticket.total_amount
        ticket.user.credit_balance += refund_amount
        
        # 5. อัปเดตสถานะและบันทึก
        # เพิ่ม Note ว่าใครเป็นคนยกเลิก
        actor = f"{current_user.username} ({current_user.role.value})"
        ticket.note = f"{ticket.note or ''} [Cancelled by {actor}]"
        
        ticket.status = TicketStatus.CANCELLED
        for item in ticket.items:
            item.status = TicketStatus.CANCELLED
            item.winning_amount = 0

        db.commit()

        # 6. บันทึก Log (จะรู้ทันทีว่าใครยกเลิก เพราะ user=current_user)
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
# เลขขายดี
@router.get("/stats/top_numbers")
def get_top_numbers(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    today = date.today()
    
    # Group by number แล้ว Sum amount
    query = db.query(
        TicketItem.number,
        func.sum(TicketItem.amount).label("total_amount"),
        func.count(TicketItem.id).label("frequency") # แทงกี่ครั้ง
    ).join(Ticket).filter(
        func.date(Ticket.created_at) == today,
        Ticket.status != 'CANCELLED' # ไม่นับบิลยกเลิก
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

# ดึงข้อมูลและคำนวณยอดสรุป ของ member โดยวนลูปจากโพยในวันนั้นๆ
@router.get("/stats/members")
def get_member_stats(
    date_str: Optional[str] = None, # รับวันที่แบบ YYYY-MM-DD (ถ้าไม่ส่งคือวันนี้)
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 1. แปลงวันที่
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = date.today()
    else:
        target_date = date.today()

    # 2. ดึง Ticket ทั้งหมดของวันนั้น (พร้อมไส้ใน Items เพื่อคำนวณยอดถูกรางวัล)
    query = db.query(Ticket).options(
        joinedload(Ticket.user), 
        joinedload(Ticket.items)
    ).filter(func.date(Ticket.created_at) == target_date)

    if current_user.role == UserRole.admin:
        query = query.filter(Ticket.shop_id == current_user.shop_id)

    tickets = query.all()

    # 3. วนลูปสรุปยอดแยกรายคน (Python Aggregation)
    stats = {}

    for t in tickets:
        if not t.user: continue # เผื่อ user deleted
        
        uid = str(t.user.id)
        if uid not in stats:
            stats[uid] = {
                "user_id": uid,
                "username": t.user.username,
                "full_name": t.user.full_name or "-",
                "total_bet": Decimal(0),      # ยอดแทงจริง (ไม่รวมยกเลิก)
                "total_win": Decimal(0),      # ยอดถูกรางวัล
                "pending_amount": Decimal(0), # ยอดรอผล
                "cancelled_amount": Decimal(0), # ยอดยกเลิก
                "bill_count": 0               # จำนวนบิลรวม
            }
        
        s = stats[uid]
        s["bill_count"] += 1
        
        # แยกยอดตามสถานะ
        if t.status == TicketStatus.CANCELLED:
            s["cancelled_amount"] += t.total_amount
        else:
            # ยอดแทงจริง (รวม PENDING, WIN, LOSE)
            s["total_bet"] += t.total_amount
            
            if t.status == TicketStatus.PENDING:
                s["pending_amount"] += t.total_amount
            elif t.status == TicketStatus.WIN:
                # คำนวณยอดรางวัลจาก Item ย่อย
                win_amt = sum(item.winning_amount for item in t.items if item.status == 'WIN')
                s["total_win"] += win_amt

    # แปลง Dict เป็น List แล้วเรียงตามยอดซื้อมากสุด
    results = list(stats.values())
    results.sort(key=lambda x: x["total_bet"], reverse=True)

    return results

@router.delete("/rates/{profile_id}")
def delete_rate_profile(
    profile_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. เช็คสิทธิ์ Admin
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. หา Profile
    profile = db.query(RateProfile).filter(RateProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Rate profile not found")

    # 3. (Optional) ป้องกันการลบถ้ามีหวยใช้งานอยู่
    # ถ้าคุณอยากให้ลบได้เลยแม้จะมีหวยใช้ (แล้วหวยพวกนั้นจะไม่มีเรท) ก็ข้ามส่วนนี้ได้
    # แต่ถ้าจะกันเหนียว:
    linked_lottos = db.query(LottoType).filter(LottoType.rate_profile_id == profile_id).count()
    if linked_lottos > 0:
        raise HTTPException(
            status_code=400, 
            detail=f"ไม่สามารถลบได้ เนื่องจากมีหวย {linked_lottos} รายการใช้งานโปรไฟล์นี้อยู่ (กรุณาเปลี่ยนเรทให้หวยเหล่านั้นก่อน)"
        )

    # 4. ลบ
    db.delete(profile)
    db.commit()
    
    return {"status": "success", "message": "Deleted successfully"}