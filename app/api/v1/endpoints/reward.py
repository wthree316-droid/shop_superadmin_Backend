from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session, joinedload
from app.api import deps
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.lotto import Ticket, TicketItem, TicketStatus, LottoResult
from app.schemas import RewardRequest, RewardResultResponse, RewardHistoryResponse
from app.core.reward_calculator import RewardCalculator
from app.core.audit_logger import write_audit_log
from decimal import Decimal
from datetime import date
from typing import List, Optional
from uuid import UUID  # [เพิ่ม] ต้อง Import UUID ด้วย

router = APIRouter()

@router.post("/issue", response_model=RewardResultResponse)
def issue_reward(
    data: RewardRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. Security Check
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 2. Check duplicate
    existing_result = db.query(LottoResult).filter(
        LottoResult.lotto_type_id == data.lotto_type_id,
        LottoResult.round_date == date.today()
    ).first()

    if existing_result:
        raise HTTPException(status_code=400, detail="Reward already issued.")

    # Save Result (เก็บ key เป็น "top", "bottom")
    new_result = LottoResult(
        lotto_type_id=data.lotto_type_id,
        round_date=date.today(),
        reward_data={"top": data.top_3, "bottom": data.bottom_2}
    )
    db.add(new_result)
    
    calc = RewardCalculator(top_3=data.top_3, bottom_2=data.bottom_2)
    
    # 3. Fetch Tickets
    pending_tickets = (
        db.query(Ticket)
        .options(joinedload(Ticket.user))
        .filter(
            Ticket.lotto_type_id == data.lotto_type_id,
            Ticket.status == TicketStatus.PENDING
        ).all()
    )

    total_winners = 0
    total_payout = Decimal('0.00')
    audit_details = []

    # 4. Check Winners
    for ticket in pending_tickets:
        is_ticket_win = False
        ticket_win_amount = Decimal('0.00')
        
        for item in ticket.items:
            if item.status == "CANCELLED": continue

            win = calc.check_is_win(bet_number=item.number, bet_type=item.bet_type)
            
            if win:
                item.status = "WIN"
                prize = item.amount * item.reward_rate
                item.winning_amount = prize
                ticket_win_amount += prize
                is_ticket_win = True
            else:
                item.status = "LOSE"
                item.winning_amount = Decimal('0.00')
            
            db.add(item)

        if is_ticket_win:
            ticket.status = TicketStatus.WIN
            ticket.user.credit_balance += ticket_win_amount
            total_winners += 1
            total_payout += ticket_win_amount
            
            audit_details.append({
                "user": ticket.user.username,
                "ticket_id": str(ticket.id),
                "win_amount": float(ticket_win_amount)
            })
        else:
            ticket.status = TicketStatus.LOSE
        
        db.add(ticket)

    # 5. Commit & Log
    try:
        db.commit()
        
        if total_winners > 0:
            background_tasks.add_task(
                write_audit_log,
                user=current_user,
                action="ISSUE_REWARD",
                target_table="lotto_results",
                details={
                    "lotto_id": str(data.lotto_type_id),
                    "top3": data.top_3,
                    "bottom2": data.bottom_2,
                    "total_payout": float(total_payout),
                    "winners_count": total_winners,
                    "sample_winners": audit_details[:5]
                },
                request=request
            )
            
    except Exception as e:
        db.rollback()
        print(f"Reward Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to process rewards")

    return {
        "total_tickets_processed": len(pending_tickets),
        "total_winners": total_winners,
        "total_payout": total_payout
    }

# [แก้ไข] เปลี่ยนชื่อฟังก์ชันและ Type Hint
@router.get("/history", response_model=List[RewardHistoryResponse])
def read_reward_history(
    skip: int = 0,
    limit: int = 20,
    lotto_type_id: Optional[UUID] = None, # ใช้ UUID เพื่อความถูกต้อง
    db: Session = Depends(get_db),
    # ไม่บังคับ Login ก็ได้เพื่อให้หน้าเว็บโชว์ผลได้เลย แต่ถ้าต้องการก็ใส่ Depends กลับมา
    # current_user: User = Depends(deps.get_current_active_user)
):
    query = db.query(LottoResult).options(joinedload(LottoResult.lotto_type))

    # กรองตามประเภทหวย (ถ้ามี)
    if lotto_type_id:
        query = query.filter(LottoResult.lotto_type_id == lotto_type_id)

    # เรียงลำดับ: วันที่ล่าสุด -> วันที่เก่า
    results = query.order_by(LottoResult.round_date.desc(), LottoResult.created_at.desc()).offset(skip).limit(limit).all()
    
    # Map ข้อมูลให้ตรงกับ Schema (RewardHistoryResponse)
    # Database เก็บ keys: "top", "bottom"
    # Schema ต้องการ keys: "top_3", "bottom_2"
    return [
        RewardHistoryResponse(
            id=r.id,
            lotto_name=r.lotto_type.name if r.lotto_type else "Unknown",
            round_date=r.round_date,
            top_3=r.reward_data.get("top"),       # Map ให้ตรง
            bottom_2=r.reward_data.get("bottom")  # Map ให้ตรง
        ) for r in results
    ]