from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.lotto import Ticket, TicketItem, TicketStatus, LottoResult, NumberRisk, LottoType
from app.schemas import RewardRequest, RewardResultResponse, RewardHistoryResponse
from app.core.config import get_thai_now, get_round_date, settings
from decimal import Decimal
from datetime import date
from typing import List, Optional, Dict
from uuid import UUID 
from app.core.game_logic import check_is_win_precise

router = APIRouter()

@router.post("/issue", response_model=RewardResultResponse)
def issue_reward(
    data: RewardRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # Security Check
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # กำหนดวันที่ (ใช้ get_round_date เพื่อคำนวณงวดที่ถูกต้องตามเวลาตัดรอบ)
    if data.round_date:
        target_date = data.round_date
    else:
        # ถ้าไม่ได้ระบุ ให้ใช้วันที่งวดปัจจุบันตามเวลาตัดรอบ
        now_thai = get_thai_now()
        target_date = get_round_date(now_thai, settings.DAY_CUTOFF_TIME)

    # 1. หา Code หวย เพื่อดึงหวยประเภทเดียวกันจากทุกร้าน
    source_lotto = db.query(LottoType).get(data.lotto_type_id)
    if not source_lotto:
        raise HTTPException(status_code=404, detail="Lotto type not found")
    
    target_code = source_lotto.code
    related_lottos = db.query(LottoType).filter(LottoType.code == target_code).all()
    related_lotto_ids = [l.id for l in related_lottos]

    # 2. บันทึก/อัปเดตผลรางวัล (LottoResult)
    for l_id in related_lotto_ids:
        existing_result = db.query(LottoResult).filter(
            LottoResult.lotto_type_id == l_id,
            LottoResult.round_date == target_date
        ).first()
        
        if existing_result:
            existing_result.top_3 = data.top_3
            existing_result.bottom_2 = data.bottom_2
            existing_result.reward_data = {"top": data.top_3, "bottom": data.bottom_2}
        else:
            new_result = LottoResult(
                lotto_type_id=l_id,
                round_date=target_date,
                top_3=data.top_3,
                bottom_2=data.bottom_2,
                reward_data={"top": data.top_3, "bottom": data.bottom_2}
            )
            db.add(new_result)
    
    # 3. 🔄 ระบบ Rollback (สำคัญมากสำหรับการแก้ผล)
    # ดึงบิล "ทั้งหมด" ของรอบนี้ (ไม่สนว่าตรวจไปแล้วหรือยัง ยกเว้นบิลที่ยกเลิก)
    all_tickets = db.query(Ticket).options(joinedload(Ticket.items)).filter(
        Ticket.lotto_type_id.in_(related_lotto_ids),
        Ticket.round_date == target_date,
        Ticket.status != TicketStatus.CANCELLED
    ).all()

    if not all_tickets:
        db.commit()
        return {"total_tickets_processed": 0, "total_winners": 0, "total_payout": 0}

    # เตรียมตัวแปรสำหรับสรุปผล
    total_payout = Decimal(0)
    win_count = 0
    
    # ตัวแปรเก็บยอดเงินที่จะต้องปรับปรุงให้ User
    # key = user_id, value = ยอดเงินสุทธิที่จะบวก/ลบ (Decimal)
    user_balance_adjustments: Dict[UUID, Decimal] = {}

    for ticket in all_tickets:
        # --- A. Rollback Phase (ดึงเงินคืนถ้าเคยถูกรางวัล) ---
        # ✅ แก้ไข: คำนวณยอดเงินรางวัลเดิมจาก TicketItem แทน (เพราะ Ticket ไม่มี field winning_amount)
        prev_win_amount = sum(item.winning_amount or 0 for item in ticket.items if item.status == TicketStatus.WIN)
        
        if ticket.status == TicketStatus.WIN and prev_win_amount > 0:
            current_adj = user_balance_adjustments.get(ticket.user_id, Decimal(0))
            user_balance_adjustments[ticket.user_id] = current_adj - prev_win_amount
        
        # รีเซ็ตสถานะบิลเพื่อเตรียมตรวจใหม่
        ticket.status = TicketStatus.PENDING
        # ไม่ต้อง reset ticket.winning_amount เพราะไม่มี column นี้

        # --- B. Calculation Phase (ตรวจรางวัลใหม่) ---
        is_ticket_win = False
        ticket_payout = Decimal(0)

        for item in ticket.items:
            # รีเซ็ตสถานะรายการย่อย
            if item.status == TicketStatus.CANCELLED: continue
            
            # ตรวจรางวัล
            is_win = check_is_win_precise(
                item.bet_type, 
                item.number, 
                data.top_3, 
                data.bottom_2
            )

            if is_win:
                item_payout = item.amount * item.reward_rate
                
                item.status = TicketStatus.WIN
                item.winning_amount = item_payout
                
                ticket_payout += item_payout
                is_ticket_win = True
            else:
                item.status = TicketStatus.LOSE
                item.winning_amount = 0

        # อัปเดตสถานะบิลหลังตรวจเสร็จ
        if is_ticket_win:
            ticket.status = TicketStatus.WIN
            # ไม่ต้อง save ticket.winning_amount
            
            win_count += 1
            total_payout += ticket_payout
            
            # เพิ่มยอดเงินรางวัลใหม่เข้าไปในรายการปรับปรุง
            current_adj = user_balance_adjustments.get(ticket.user_id, Decimal(0))
            user_balance_adjustments[ticket.user_id] = current_adj + ticket_payout
        else:
            ticket.status = TicketStatus.LOSE
            # ไม่ต้อง save ticket.winning_amount

    # 4. บันทึกการเปลี่ยนแปลงเงิน User
    for uid, amount in user_balance_adjustments.items():
        if amount != 0:
            user = db.query(User).filter(User.id == uid).first()
            if user:
                user.credit_balance += amount
                # บันทึก log การเงินเพิ่มเติมตรงนี้ได้ถ้ามีตาราง transaction

    db.commit()

    return {
        "success": True,
        "total_tickets_processed": len(all_tickets),
        "total_winners": win_count,
        "total_payout": total_payout
    }


@router.get("/daily") 
def get_daily_rewards(
    date: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    results = db.query(LottoResult).filter(
        LottoResult.round_date == date
    ).all()
    
    return {
        str(r.lotto_type_id): {
            "top_3": r.top_3 or (r.reward_data.get("top") if r.reward_data else ""), 
            "bottom_2": r.bottom_2 or (r.reward_data.get("bottom") if r.reward_data else ""),
            "created_at": r.created_at
        } 
        for r in results
    }

@router.get("/history")
def get_reward_history(
    lotto_type_id: UUID,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    results = db.query(LottoResult).filter(
        LottoResult.lotto_type_id == lotto_type_id
    ).order_by(LottoResult.round_date.desc()).limit(limit).all()
    
    return [
        {
            "round_date": r.round_date,
            "top_3": r.top_3 or (r.reward_data.get("top") if r.reward_data else ""),
            "bottom_2": r.bottom_2 or (r.reward_data.get("bottom") if r.reward_data else "")
        }
        for r in results
    ]