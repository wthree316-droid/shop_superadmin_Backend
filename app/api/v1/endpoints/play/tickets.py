from decimal import Decimal
from typing import List, Optional
from datetime import datetime, time, date, timedelta
from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy import desc

from app.api import deps
from app.schemas import TicketCreate, TicketResponse
from app.db.session import get_db
from app.models.lotto import Ticket, TicketItem, LottoType, TicketStatus, NumberRisk
from app.models.user import User, UserRole
from app.core.config import get_thai_now, get_round_date, settings

router = APIRouter()

@router.post("/submit_ticket", response_model=TicketResponse)
def submit_ticket(
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

    # 3. ตรวจสอบเวลาปิด (Strict Check) รองรับข้ามวัน
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

    day_map = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
    allowed_days = [day_map[d] for d in (lotto.open_days or [])]
    is_today_open = today_date.weekday() in allowed_days

    # ตรวจสอบว่าหวยข้ามวันหรือไม่ (เช่น เปิด 08:00 ปิด 00:10)
    is_overnight = False
    if open_time_obj and close_time_obj:
        # ถ้าเวลาปิด < เวลาเปิด แสดงว่าข้ามวัน
        if close_time_obj < open_time_obj:
            is_overnight = True
    
    # ถ้าวันนี้เปิด แต่เลยเวลาปิด -> Error
    if is_today_open and close_time_obj:
        if is_overnight:
            # กรณีข้ามวัน: ปิดรับเมื่อ เวลา >= เวลาเปิด และเวลา > เวลาปิด
            # หรือ เวลา < เวลาเปิด และเวลา > เวลาปิด
            # ให้เปิดรับได้ถ้า: เวลา >= เวลาเปิด หรือ เวลา <= เวลาปิด
            if not (now_time >= open_time_obj or now_time <= close_time_obj):
                raise HTTPException(
                    status_code=400, 
                    detail=f"ปิดรับแทงแล้วครับ (ปิด {t_str[:5]} น.)"
                )
        else:
            # กรณีปกติ (ไม่ข้ามวัน)
            if now_time > close_time_obj:
                raise HTTPException(
                    status_code=400, 
                    detail=f"ปิดรับแทงแล้วครับ (ปิด {t_str[:5]} น.)"
                )

    # 4. คำนวณงวด (รองรับการตัดรอบวันใหม่)
    rules = lotto.rules if lotto.rules else {} 
    schedule_type = rules.get('schedule_type', 'weekly')
    
    # ใช้ get_round_date() เพื่อคำนวณงวดที่ถูกต้องตามเวลาตัดรอบ
    target_round_date = get_round_date(now_thai, settings.DAY_CUTOFF_TIME)

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
        if not is_today_open:
             raise HTTPException(status_code=400, detail="วันนี้ไม่มีรอบเปิดรับแทง")
        # target_round_date ถูกคำนวณจาก get_round_date() แล้วด้านบน

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

    for item_in in ticket_in.items:
        check_key = f"{item_in.number}:{item_in.bet_type}"
        check_key_all = f"{item_in.number}:ALL"
        risk_status = risk_map.get(check_key) or risk_map.get(check_key_all)

        rate_config = rates.get(item_in.bet_type, {})
        base_pay = Decimal(0)
        min_bet = Decimal("1")
        max_bet = Decimal("0")

        if isinstance(rate_config, (int, float, str, Decimal)):
            base_pay = Decimal(str(rate_config))
        else:
            base_pay = Decimal(str(rate_config.get('pay', 0)))
            min_bet = Decimal(str(rate_config.get('min', 1)))
            max_bet = Decimal(str(rate_config.get('max', 0)))

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

    # 6. ตัดเงินและบันทึกบิล
    user_db = db.query(User).filter(User.id == current_user.id).with_for_update().first()
    current_credit = Decimal(str(user_db.credit_balance))

    if current_credit < total_amount:
        raise HTTPException(status_code=400, detail=f"ยอดเงินไม่พอ (ขาด {total_amount - current_credit:,.2f} บาท)")

    try:
        user_db.credit_balance = current_credit - total_amount

        comm_pct = Decimal(str(user_db.commission_percent or 0))
        comm_amount = (total_amount * comm_pct) / Decimal('100')

        # db.add(current_user) #ไม่แน่ใจเก็บไว้ก่อน

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
        db.flush() 

        for p_item in processed_items:
            t_item = TicketItem(
                ticket_id=new_ticket.id,
                number=p_item["number"],
                bet_type=p_item["bet_type"],
                amount=p_item["amount"],
                reward_rate=p_item["reward_rate"],
                winning_amount=0,
                status=TicketStatus.PENDING
            )
            db.add(t_item)

        db.commit()
        db.refresh(new_ticket)
        return new_ticket

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

@router.get("/history", response_model=List[TicketResponse])
def read_history(
    skip: int = 0,
    limit: int = 100,
    lotto_type_id: Optional[UUID] = None,
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
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
    
    # กรองตามสถานะ (ถ้ามีการระบุ)
    if status and status != 'ALL':
        query = query.filter(Ticket.status == status)

    return query.order_by(Ticket.created_at.desc()).offset(skip).limit(limit).all()

@router.get("/shop_history", response_model=List[TicketResponse])
def get_shop_tickets(
    skip: int = 0,
    limit: int = 100,
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

    return query.order_by(Ticket.created_at.desc()).offset(skip).limit(limit).all()