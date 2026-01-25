from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.lotto import Ticket, TicketItem, TicketStatus, LottoResult, NumberRisk
from app.schemas import RewardRequest, RewardResultResponse, RewardHistoryResponse
from app.core.config import get_thai_now
from decimal import Decimal
from datetime import date
from typing import List, Optional, Dict
from uuid import UUID 

router = APIRouter()

def check_is_win(bet_type: str, number: str, top_3: str, bottom_2: str) -> bool:
    try:
        if bet_type == '3top':      return number == top_3
        elif bet_type == '3tod':    return sorted(number) == sorted(top_3)
        elif bet_type == '2up':     return number == top_3[-2:]
        elif bet_type == '2down':   return number == bottom_2
        elif bet_type == 'run_up':  return number in top_3
        elif bet_type == 'run_down': return number in bottom_2
        return False
    except:
        return False

@router.post("/issue", response_model=RewardResultResponse)
def issue_reward(
    data: RewardRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    target_date = data.round_date if data.round_date else get_thai_now().date()

    # 1. บันทึกผลรางวัล
    existing_result = db.query(LottoResult).filter(
        LottoResult.lotto_type_id == data.lotto_type_id,
        LottoResult.round_date == target_date
    ).first()
    
    if existing_result:
        existing_result.reward_data = {"top": data.top_3, "bottom": data.bottom_2}
    else:
        new_result = LottoResult(
            lotto_type_id=data.lotto_type_id,
            round_date=target_date,
            reward_data={"top": data.top_3, "bottom": data.bottom_2}
        )
        db.add(new_result)
    
    db.commit()

    # 2. ดึงข้อมูลเลขอั้น/เลขปิด (Number Risk) มาเตรียมไว้เช็ค Double Check
    risk_entries = db.query(NumberRisk).filter(
        NumberRisk.lotto_type_id == data.lotto_type_id
        # ❌ อย่าใส่ filter(created_at...) เด็ดขาด
    ).all()
    
    risk_lookup = {}
    for r in risk_entries:
        key = f"{r.number}:{r.specific_bet_type or 'ALL'}"
        risk_lookup[key] = r.risk_type

    # 3. ดึงโพย PENDING มาตรวจ
    pending_tickets = db.query(Ticket).options(joinedload(Ticket.items)).filter(
        Ticket.lotto_type_id == data.lotto_type_id,
        Ticket.round_date == target_date,
        Ticket.status == TicketStatus.PENDING
    ).all()

    if not pending_tickets:
        return {"total_tickets_processed": 0, "total_winners": 0, "total_payout": 0}

    item_updates = []         
    ticket_updates = []       
    user_updates: Dict[UUID, Decimal] = {}  

    total_payout = Decimal(0)
    win_count = 0

    for ticket in pending_tickets:
        is_ticket_win = False
        ticket_payout = Decimal(0)
        
        for item in ticket.items:
            if item.status == TicketStatus.CANCELLED: continue

            # ตรวจรางวัลเบื้องต้น
            is_win = check_is_win(item.bet_type, item.number, data.top_3, data.bottom_2)
            
            # 🔥 Double Check: ถ้าถูกรางวัล ต้องเช็คด้วยว่าติดเลขอั้น (CLOSE) หรือไม่
            if is_win:
                s_key = f"{item.number}:{item.bet_type}"
                g_key = f"{item.number}:ALL"
                risk = risk_lookup.get(s_key) or risk_lookup.get(g_key)
                
                # ถ้าเจอ CLOSE ให้ปรับเป็นแพ้ทันที (โมฆะรางวัล)
                if risk == 'CLOSE':
                    is_win = False
            
            # คำนวณเงิน
            item_payout = Decimal(0)
            if is_win:
                item_payout = item.amount * item.reward_rate
                ticket_payout += item_payout
                is_ticket_win = True

            item_status = TicketStatus.WIN if is_win else TicketStatus.LOSE
            item_updates.append({
                "id": item.id, 
                "status": item_status, 
                "winning_amount": item_payout
            })
        
        ticket_status = TicketStatus.WIN if is_ticket_win else TicketStatus.LOSE
        ticket_updates.append({"id": ticket.id, "status": ticket_status})

        if is_ticket_win:
            win_count += 1
            total_payout += ticket_payout
            current_val = user_updates.get(ticket.user_id, Decimal(0))
            user_updates[ticket.user_id] = current_val + ticket_payout

    # Execute Update
    try:
        if item_updates: db.bulk_update_mappings(TicketItem, item_updates)
        if ticket_updates: db.bulk_update_mappings(Ticket, ticket_updates)
        for uid, amount in user_updates.items():
            db.query(User).filter(User.id == uid).update(
                {User.credit_balance: User.credit_balance + amount}, synchronize_session=False
            )
        db.commit() 

        return {
            "total_tickets_processed": len(pending_tickets),
            "total_winners": win_count,
            "total_payout": total_payout
        }

    except Exception as e:
        db.rollback()
        print(f"Error issuing reward: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดในการคำนวณรางวัล")


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
            "top_3": r.reward_data.get("top") if r.reward_data else "", 
            "bottom_2": r.reward_data.get("bottom") if r.reward_data else "",
            "created_at": r.created_at
        } 
        for r in results
    }