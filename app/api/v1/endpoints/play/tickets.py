from decimal import Decimal
from typing import List, Optional
from datetime import datetime, time, date, timedelta
from uuid import UUID
from sqlalchemy.orm import Session, joinedload, selectinload, noload
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy import desc
from app.core.limiter import limiter
from app.api import deps
from app.schemas import TicketCreate, TicketResponse
from app.db.session import get_db
from app.models.lotto import Ticket, TicketItem, LottoType, TicketStatus, NumberRisk
from app.models.user import User, UserRole
from app.core.config import get_thai_now, get_round_date, settings
import hashlib
import json
from app.core.history_cache import get_or_set_history

router = APIRouter()

@router.post("/submit_ticket", response_model=TicketResponse)
@limiter.limit("30/minute")
def submit_ticket(
    request: Request,
    ticket_in: TicketCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. ระบุ Shop ID
    target_shop_id = current_user.shop_id
    if current_user.role == UserRole.superadmin and ticket_in.shop_id:
        target_shop_id = ticket_in.shop_id
    elif current_user.role == UserRole.admin:
        target_shop_id = current_user.shop_id

    # 2. ดึงข้อมูลหวย
    lotto = db.query(LottoType).filter(LottoType.id == ticket_in.lotto_type_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="ไม่พบประเภทหวย")
    
    if not lotto.is_active:
         raise HTTPException(status_code=400, detail="หวยนี้ปิดรับแทงชั่วคราว (Closed)")

    now_thai = get_thai_now()
    today_date = now_thai.date()
    now_time = now_thai.time()

    # 3. ตรวจสอบเวลาเปิด (Strict Check) รองรับข้ามวัน
    close_time_obj = None
    open_time_obj = None
    
    if lotto.close_time:
        try:
            t_str = str(lotto.close_time)
            if len(t_str) == 5: t_str += ":00"
            close_time_obj = datetime.strptime(t_str, "%H:%M:%S").time()
        except:
            pass
    
    if lotto.open_time:
        try:
            o_str = str(lotto.open_time)
            if len(o_str) == 5: o_str += ":00"
            open_time_obj = datetime.strptime(o_str, "%H:%M:%S").time()
        except:
            pass

    # ตรวจสอบว่าหวยข้ามวันหรือไม่ (เช่น เปิด 20:00 ปิด 00:30)
    is_overnight = False
    if open_time_obj and close_time_obj:
        if close_time_obj < open_time_obj:
            is_overnight = True

    # 🌟 4. คำนวณงวด (Round Date) ให้ฉลาดขึ้นสำหรับหวยข้ามวัน
    target_round_date = get_round_date(now_thai, settings.DAY_CUTOFF_TIME)
    
    if is_overnight:
        if now_time <= close_time_obj:
            # ถ้าแทงหลังเที่ยงคืน แต่ยังไม่ถึงเวลาปิด -> ให้บังคับเป็นงวดของเมื่อวาน
            target_round_date = today_date - timedelta(days=1)
        elif now_time >= open_time_obj:
            # ถ้าแทงก่อนเที่ยงคืน (หลังเวลาเปิด) -> ให้เป็นงวดของวันนี้ตามปกติ
            target_round_date = today_date

    # 🌟 5. ตรวจสอบว่า "งวดนี้" เปิดรับแทงหรือไม่ (ใช้วันที่ของงวดแทนวันที่กดแทง เพื่อไม่ให้ข้ามวันแล้วพัง)
    day_map = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
    allowed_days = [day_map[d] for d in (lotto.open_days or [])]
    is_round_open = target_round_date.weekday() in allowed_days

    # 6. ตรวจสอบเวลาว่า "หมดเวลาหรือยัง"
    if close_time_obj:
        if is_overnight:
            # กรณีข้ามวัน: ต้องอยู่ในช่วงเวลา (>= เวลาเปิด) หรือ (<= เวลาปิด)
            if not (now_time >= open_time_obj or now_time <= close_time_obj):
                raise HTTPException(status_code=400, detail=f"ปิดรับแทงแล้วครับ (ปิด {t_str[:5]} น.)")
        else:
            # กรณีปกติ: ถ้าเวลาปัจจุบันมากกว่าเวลาปิด คือหมดเวลา
            if now_time > close_time_obj:
                raise HTTPException(status_code=400, detail=f"ปิดรับแทงแล้วครับ (ปิด {t_str[:5]} น.)")

    # 7. จัดการหวยรายเดือน vs รายวัน
    rules = lotto.rules if lotto.rules else {} 
    schedule_type = rules.get('schedule_type', 'weekly')
    
    if schedule_type == 'monthly':
        # สำหรับหวยรายเดือน (เช่น หวยรัฐบาล)
        close_dates = rules.get('close_dates', [1, 16])
        target_dates = sorted([int(d) for d in close_dates])
        current_day = now_thai.day
        found_date = -1
        for d in target_dates:
            if d > current_day:
                found_date = d
                break
            if d == current_day:
                 if close_time_obj and now_time < close_time_obj:
                     found_date = d
                     break
        
        if found_date == -1:
             next_month = now_thai.replace(day=28) + timedelta(days=4) 
             found_date = target_dates[0]
             target_round_date = date(next_month.year, next_month.month, found_date)
        else:
             target_round_date = date(now_thai.year, now_thai.month, found_date)
    else:
        # สำหรับหวยรายวัน (weekly/daily)
        if not is_round_open:
             raise HTTPException(status_code=400, detail="งวดนี้ไม่มีรอบเปิดรับแทง")

    # 5. ตรวจเลขอั้น
    r_start = datetime.combine(target_round_date, time.min) - timedelta(hours=7)
    r_end = datetime.combine(target_round_date, time.max) - timedelta(hours=7)

    daily_risks = db.query(NumberRisk).filter(
        NumberRisk.lotto_type_id == ticket_in.lotto_type_id,
        NumberRisk.shop_id == target_shop_id, 
        NumberRisk.created_at >= r_start,
        NumberRisk.created_at <= r_end
    ).all()

    risk_map = {}
    for r in daily_risks:
        risk_map[f"{r.number}:{r.specific_bet_type}"] = r.risk_type
        risk_map[f"{r.number}:ALL"] = r.risk_type

    rates = {}
    if lotto.rate_profile and lotto.rate_profile.rates:
        rates = lotto.rate_profile.rates

    processed_items = []
    total_amount = Decimal(0)

    # 🌟 ฟังก์ชันช่วยแปลงตัวเลขให้ปลอดภัย (ดักจับกรณีเป็นค่าว่าง "")
    def safe_dec(val, default_val):
        try:
            return Decimal(str(val)) if str(val).strip() != "" else Decimal(str(default_val))
        except:
            return Decimal(str(default_val))

    for item_in in ticket_in.items:
        check_key = f"{item_in.number}:{item_in.bet_type}"
        check_key_all = f"{item_in.number}:ALL"
        risk_status = risk_map.get(check_key) or risk_map.get(check_key_all)

        rate_config = rates.get(item_in.bet_type, {})
        base_pay = Decimal(0)
        min_bet = Decimal("1")
        max_bet = Decimal("0")

        # 🚀 ใช้ฟังก์ชันช่วยแปลงตัวเลขแทนการครอบ Decimal ตรงๆ
        if isinstance(rate_config, (int, float, str, Decimal)):
            base_pay = safe_dec(rate_config, 0)
        else:
            base_pay = safe_dec(rate_config.get('pay'), 0)
            min_bet = safe_dec(rate_config.get('min'), 1)
            max_bet = safe_dec(rate_config.get('max'), 0)

        final_amount = Decimal(str(item_in.amount)) 
        final_rate = base_pay
        
        if risk_status == "CLOSE":
            final_amount = Decimal(0)
            final_rate = Decimal(0)
        elif risk_status == "HALF":
            final_rate = base_pay / 2
            if final_amount < min_bet:
                raise HTTPException(status_code=400, detail=f"แทงขั้นต่ำ {min_bet:,.0f} บาท ({item_in.bet_type})")
            if max_bet > 0 and final_amount > max_bet:
                raise HTTPException(status_code=400, detail=f"แทงสูงสุด {max_bet:,.0f} บาท ({item_in.bet_type})")
        else:
            if base_pay == 0:
                 raise HTTPException(status_code=400, detail=f"ไม่พบอัตราจ่ายสำหรับ: {item_in.bet_type}")
            if final_amount < min_bet:
                raise HTTPException(status_code=400, detail=f"แทงขั้นต่ำ {min_bet:,.0f} บาท ({item_in.bet_type})")
            if max_bet > 0 and final_amount > max_bet:
                raise HTTPException(status_code=400, detail=f"แทงสูงสุด {max_bet:,.0f} บาท ({item_in.bet_type})")

        processed_items.append({
            "number": item_in.number,
            "bet_type": item_in.bet_type,
            "amount": final_amount,
            "reward_rate": final_rate
        })
        total_amount += final_amount

    # 6. ตรวจสอบยอดเงินเบื้องต้น (แบบไม่ Lock เพื่อให้คืนค่า Error ไวที่สุดถ้าเงินไม่พอ)
    if Decimal(str(current_user.credit_balance)) < total_amount:
        raise HTTPException(
            status_code=400, 
            detail=f"ยอดเงินไม่พอ (ขาด {total_amount - Decimal(str(current_user.credit_balance)):,.2f} บาท)"
        )

    try:
        comm_pct = Decimal(str(current_user.commission_percent or 0))
        comm_amount = (total_amount * comm_pct) / Decimal('100')

        # 7. สร้างบิลหลัก
        new_ticket = Ticket(
            shop_id=target_shop_id,
            user_id=current_user.id,
            lotto_type_id=ticket_in.lotto_type_id,
            round_date=target_round_date,
            note=ticket_in.note,
            total_amount=total_amount,
            commission_amount=comm_amount,
            status=TicketStatus.PENDING
        )
        db.add(new_ticket)
        db.flush() # ดันข้อมูลเข้า DB เพื่อให้ได้ new_ticket.id มาใช้งานก่อน

        # 8. 🚀 ใช้ Bulk Insert สำหรับรายการเลขแทง (แก้ปัญหา N+1 ยิง SQL รวดเดียว!)
        items_to_insert = [
            TicketItem(
                ticket_id=new_ticket.id,
                number=p["number"],
                bet_type=p["bet_type"],
                amount=p["amount"],
                reward_rate=p["reward_rate"],
                winning_amount=0,
                status=TicketStatus.PENDING
            ) for p in processed_items
        ]
        db.bulk_save_objects(items_to_insert)

        # 9. ⚡ ล็อกข้อมูล User (with_for_update) ตอนท้ายสุด เพื่อลดระยะเวลาการล็อกให้น้อยที่สุด
        user_db = db.query(User).filter(User.id == current_user.id).with_for_update().first()
        current_credit = Decimal(str(user_db.credit_balance))
        
        # เช็คยอดเงินอีกครั้งเพื่อความชัวร์ (ป้องกันกรณีลูกค้ากดยิงบิลพร้อมกัน 2 หน้าต่างในเสี้ยววินาที)
        if current_credit < total_amount:
            raise HTTPException(status_code=400, detail="ยอดเงินไม่พอ กรุณาเติมเครดิต")
            
        # หักเงิน
        user_db.credit_balance = current_credit - total_amount

        db.commit()
        db.refresh(new_ticket)
        return new_ticket

    except HTTPException as he:
        db.rollback()
        raise he
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"ระบบขัดข้อง: {str(e)}")

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

    # ตรวจสอบสิทธิ์การยกเลิก:
    # 1. Superadmin ยกเลิกได้ทุกอย่าง
    # 2. Admin หรือ Member ยกเลิกได้เฉพาะใน Shop ตัวเอง (Member สามารถยกเลิกบิลของคนอื่นใน Shop ได้ตาม requirement)
    
    if current_user.role == UserRole.superadmin:
        pass
    elif current_user.role in [UserRole.admin, UserRole.member]:
        # ต้องเป็นร้านเดียวกันเท่านั้น
        if ticket.shop_id != current_user.shop_id:
            raise HTTPException(status_code=403, detail="Cross-shop action denied")
    else:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        # คำนวณเงินที่จะคืนและเงินที่จะดึงกลับ
        refund_amount = Decimal(ticket.total_amount)
        reclaim_reward = Decimal(0)

        # ถ้าบิลเคยถูกรางวัลและจ่ายเงินไปแล้ว ต้องดึงเงินรางวัลคืน
        if ticket.status == TicketStatus.WIN:
            for item in ticket.items:
                if item.winning_amount and item.winning_amount > 0:
                    reclaim_reward += Decimal(item.winning_amount)
        
        # อัพเดทเครดิต: คืนค่าโพย - เงินรางวัลที่ต้องดึงคืน
        net_change = refund_amount - reclaim_reward
        ticket.user.credit_balance += net_change
        
        actor = f"{current_user.username} ({current_user.role.value})"
        ticket.note = f"{ticket.note or ''} [Cancelled by {actor}] (Refund: {refund_amount}, Reclaim: {reclaim_reward})"
        
        ticket.status = TicketStatus.CANCELLED
        for item in ticket.items:
            item.status = TicketStatus.CANCELLED
            item.winning_amount = 0

        db.commit()
        return {
            "status": "success", 
            "message": "Ticket cancelled", 
            "refunded_cost": refund_amount,
            "reclaimed_reward": reclaim_reward,
            "net_credit_change": net_change
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to cancel ticket")

@router.get("/history")
def read_history(
    skip: int = 0,
    limit: int = 200,
    lotto_type_id: Optional[UUID] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. จัดการวันที่ (ใช้วันนี้เป็น Default)
    thai_today = get_thai_now().date()
    s_date_str = start_date or date or thai_today.strftime("%Y-%m-%d")
    e_date_str = end_date or date or thai_today.strftime("%Y-%m-%d")

    # 🌟 2. เช็คว่าเป็น "อดีต" ล้วนๆ หรือไม่?
    # ถ้า end_date น้อยกว่า วันนี้ แปลว่าข้อมูลไม่มีทางอัปเดตแล้ว (หวยออกไปแล้ว)
    e_date_obj = datetime.strptime(e_date_str, "%Y-%m-%d").date()
    is_past = e_date_obj < thai_today

    # 3. สร้าง Cache Key (เหมือนรหัสบัตรประชาชนของ Request นี้)
    key_dict = {
        "user_id": str(current_user.id), "skip": skip, "limit": limit,
        "start": s_date_str, "end": e_date_str, 
        "lotto": str(lotto_type_id) if lotto_type_id else "ALL",
        "status": status or "ALL"
    }
    # เข้ารหัสให้สั้นลง
    cache_key = f"history_client_{hashlib.md5(json.dumps(key_dict, sort_keys=True).encode()).hexdigest()}"

    # 4. ฟังก์ชันดึง Database (จะถูกเรียกก็ต่อเมื่อไม่มี Cache)
    def fetch_from_db():
        try:
            s_d = datetime.strptime(s_date_str, "%Y-%m-%d").date()
            e_d = datetime.strptime(e_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")

        query = db.query(Ticket).options(
            noload(Ticket.items),
            joinedload(Ticket.lotto_type)
        ).filter(
            Ticket.user_id == current_user.id,
            # 🚀 เปลี่ยนมาค้นหาจาก "งวดวันที่" แทน "เวลากดแทงจริง"
            Ticket.round_date >= s_d,
            Ticket.round_date <= e_d
        )

        if lotto_type_id: query = query.filter(Ticket.lotto_type_id == lotto_type_id)
        if status and status != 'ALL': query = query.filter(Ticket.status == status)

        orm_results = query.order_by(Ticket.created_at.desc()).offset(skip).limit(limit).all()
        # 🚀 เพิ่ม from_attributes=True เข้าไปในวงเล็บ
        return [TicketResponse.model_validate(t, from_attributes=True).model_dump() for t in orm_results]
    
    # 5. เรียกใช้สมองกล
    return get_or_set_history(cache_key, is_past, fetch_from_db)

@router.get("/shop_history")
def get_shop_tickets(
    skip: int = 0,
    limit: int = 200,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if not current_user.shop_id:
         raise HTTPException(status_code=400, detail="No shop assigned")

    thai_today = get_thai_now().date()
    s_date_str = start_date or date or thai_today.strftime("%Y-%m-%d")
    e_date_str = end_date or date or thai_today.strftime("%Y-%m-%d")

    e_date_obj = datetime.strptime(e_date_str, "%Y-%m-%d").date()
    is_past = e_date_obj < thai_today

    key_dict = {
        "shop_id": str(current_user.shop_id), "skip": skip, "limit": limit,
        "start": s_date_str, "end": e_date_str, 
        "user": str(user_id) if user_id else "ALL"
    }
    cache_key = f"history_shop_{hashlib.md5(json.dumps(key_dict, sort_keys=True).encode()).hexdigest()}"

    # ในฟังก์ชัน get_shop_tickets -> fetch_from_db()
    def fetch_from_db():
        try:
            s_d = datetime.strptime(s_date_str, "%Y-%m-%d").date()
            e_d = datetime.strptime(e_date_str, "%Y-%m-%d").date()
            # ลบ target_start, target_end บรรทัดเดิมทิ้งไปได้เลยครับ
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")

        query = db.query(Ticket).options(
                noload(Ticket.items),
                joinedload(Ticket.user),
                joinedload(Ticket.lotto_type),   
            ).filter(
                Ticket.shop_id == current_user.shop_id,
                # 🚀 เปลี่ยนมาค้นหาจาก "งวดวันที่" แทน
                Ticket.round_date >= s_d,
                Ticket.round_date <= e_d
            )

        if user_id: query = query.filter(Ticket.user_id == user_id)

        orm_results = query.order_by(Ticket.created_at.desc()).offset(skip).limit(limit).all()
        # 🚀 เพิ่ม from_attributes=True เข้าไปในวงเล็บ
        return [TicketResponse.model_validate(t, from_attributes=True).model_dump() for t in orm_results]

    return get_or_set_history(cache_key, is_past, fetch_from_db)

# 🚀 API ใหม่: สำหรับดึงรายการเลขแทงเฉพาะบิลที่ลูกค้าต้องการดูรายละเอียด
@router.get("/tickets/{ticket_id}/items")
def get_ticket_items(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. เช็คว่าบิลมีจริงไหม
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="ไม่พบโพย")
        
    # 🌟 2. กำแพงตรวจสอบสิทธิ์ (อนุญาตแค่คนในร้านเดียวกัน)
    if current_user.role != UserRole.superadmin:
        # ถ้าไม่ใช่ Superadmin ต้องตรวจบัตรว่า "อยู่ร้านเดียวกันไหม?"
        if ticket.shop_id != current_user.shop_id:
            raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์ดูโพยของร้านอื่น")

    # 3. ดึงรายการเลขของบิลนี้ส่งกลับไป
    items = db.query(TicketItem).filter(TicketItem.ticket_id == ticket_id).all()
    
    return items