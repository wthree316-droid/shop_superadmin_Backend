from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.api import deps
from app.db.session import get_db, SessionLocal
from app.models.user import User, UserRole
from app.models.lotto import Ticket, TicketItem, TicketStatus, LottoResult, NumberRisk, LottoType
from app.schemas import RewardRequest, RewardResultResponse, RewardHistoryResponse
from app.core.config import get_thai_now, get_round_date, settings
from decimal import Decimal
from datetime import date
from typing import List, Optional, Dict
from uuid import UUID 
from app.core.game_logic import check_is_win_precise
from app.core.history_cache import get_or_set_history, clear_all_history_cache
from app.core.stats_cache import invalidate_stats_cache  # 🌟 เพิ่มคำสั่งนี้

router = APIRouter()

# ==========================================
# 🌟 ฟังก์ชันสำหรับทำงานเบื้องหลัง (Background Worker)
# ==========================================

def process_reward_background(target_code: str, target_date: date, top_3: str, bottom_2: str):
    # ต้องเปิด Session ใหม่ เพราะ Session ของ API เดิมจะถูกปิดไปแล้วตอนส่ง Response
    db = SessionLocal() 
    try:
        # 1. หา Code หวย เพื่อดึงหวยประเภทเดียวกันจากทุกร้าน
        related_lottos = db.query(LottoType).filter(LottoType.code == target_code).all()
        related_lotto_ids = [l.id for l in related_lottos]
        
        # 2. 🔄 ระบบ Rollback (ดึงบิลทั้งหมดของรอบนี้)
        all_tickets = db.query(Ticket).options(joinedload(Ticket.items)).filter(
            Ticket.lotto_type_id.in_(related_lotto_ids),
            Ticket.round_date == target_date,
            Ticket.status != TicketStatus.CANCELLED
        ).all()

        if not all_tickets:
            db.commit()
            return

        total_payout = Decimal(0)
        win_count = 0
        user_balance_adjustments: Dict[UUID, Decimal] = {}

        for ticket in all_tickets:
            # --- A. Rollback Phase (ดึงเงินคืนถ้าเคยถูกรางวัล) ---
            prev_win_amount = sum(item.winning_amount or 0 for item in ticket.items if item.status == TicketStatus.WIN)
            
            if ticket.status == TicketStatus.WIN and prev_win_amount > 0:
                current_adj = user_balance_adjustments.get(ticket.user_id, Decimal(0))
                user_balance_adjustments[ticket.user_id] = current_adj - prev_win_amount
            
            ticket.status = TicketStatus.PENDING
            ticket.winning_amount = 0 # 🌟 1. รีเซ็ตยอดเก่าเป็น 0 (กรณีคำนวณใหม่)

            # --- B. Calculation Phase (ตรวจรางวัลใหม่) ---
            is_ticket_win = False
            ticket_payout = Decimal(0)

            for item in ticket.items:
                if item.status == TicketStatus.CANCELLED: continue
                
                is_win = check_is_win_precise(
                    item.bet_type, 
                    item.number, 
                    top_3, 
                    bottom_2
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

            # อัปเดตสถานะบิล
            if is_ticket_win:
                ticket.status = TicketStatus.WIN
                ticket.winning_amount = ticket_payout # 🌟 2. เซฟยอดที่ถูกรางวัลเข้าบิล
                win_count += 1
                total_payout += ticket_payout
                
                current_adj = user_balance_adjustments.get(ticket.user_id, Decimal(0))
                user_balance_adjustments[ticket.user_id] = current_adj + ticket_payout
            else:
                ticket.status = TicketStatus.LOSE
                ticket.winning_amount = 0

        # 4. 🚀 บันทึกการเปลี่ยนแปลงเงิน User (แบบรวดเดียวจบ)
        winning_uids = [uid for uid, amount in user_balance_adjustments.items() if amount != 0]
        
        if winning_uids:
            # ดึงข้อมูล User ที่ได้เงิน/เสียเงิน "ทั้งหมด" มารวดเดียว (ใช้ with_for_update เพื่อล็อคป้องกันเงินรวน)
            affected_users = db.query(User).filter(User.id.in_(winning_uids)).with_for_update().all()
            
            # บวก/ลบ เงินในหน่วยความจำ
            for user in affected_users:
                user.credit_balance += user_balance_adjustments[user.id]

        db.commit() # เซฟลง Database รวดเดียวจบ!
        
        clear_all_history_cache()
        invalidate_stats_cache()
        print(f"✅ Background Reward Issue Success! Processed {len(all_tickets)} tickets.")

    except Exception as e:
        db.rollback()
        print(f"❌ Background Reward Issue Failed: {str(e)}")
    finally:
        db.close() # 🔴 สำคัญมาก ต้องปิดสาย DB ทุกครั้งที่งานเสร็จ


# ==========================================
# 🚀 API หลัก (ตอบกลับไวใน 0.1 วินาที)
# ==========================================
@router.post("/issue")
def issue_reward(
    data: RewardRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if data.round_date:
        target_date = data.round_date
    else:
        now_thai = get_thai_now()
        target_date = get_round_date(now_thai, settings.DAY_CUTOFF_TIME)

    source_lotto = db.query(LottoType).get(data.lotto_type_id)
    if not source_lotto:
        raise HTTPException(status_code=404, detail="Lotto type not found")
        
    # 🌟 1. [เพิ่มใหม่] เซฟเลขรางวัลลงตาราง LottoResult "ทันที" เพื่อให้หน้าเว็บเห็นเลขปุ๊บปั๊บ
    related_lottos = db.query(LottoType).filter(LottoType.code == source_lotto.code).all()
    
    for lotto in related_lottos:
        existing_result = db.query(LottoResult).filter(
            LottoResult.lotto_type_id == lotto.id,
            LottoResult.round_date == target_date
        ).first()
        
        if existing_result:
            existing_result.top_3 = data.top_3
            existing_result.bottom_2 = data.bottom_2
            existing_result.reward_data = {"top": data.top_3, "bottom": data.bottom_2}
        else:
            new_result = LottoResult(
                lotto_type_id=lotto.id,
                round_date=target_date,
                top_3=data.top_3,
                bottom_2=data.bottom_2,
                reward_data={"top": data.top_3, "bottom": data.bottom_2}
            )
            db.add(new_result)
            
    db.commit() # เซฟเลขลง Database ทันที!

    # 🌟 เปลี่ยนจากการโยนเข้า Background Task เป็นการเรียกใช้งานตรงๆ ไปเลย
    process_reward_background(
        target_code=source_lotto.code,
        target_date=target_date,
        top_3=data.top_3,
        bottom_2=data.bottom_2
    )

    return {
        "success": True,
        "message": "บันทึกตัวเลขและคำนวณเงินรางวัลให้ลูกค้าเสร็จสมบูรณ์!"
    }


@router.get("/daily") 
def get_daily_rewards(
    date: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. ดึงผลรางวัลทั้งหมดของวันนี้ พร้อมข้อมูลหวยมาด้วย
    results = db.query(LottoResult, LottoType).join(
        LottoType, LottoResult.lotto_type_id == LottoType.id
    ).filter(
        LottoResult.round_date == date
    ).all()
    
    # 2. จำผลรางวัลไว้ในหน่วยความจำโดยอ้างอิงจาก "รหัสหวย (Code)" เช่น THAI, LAOS
    code_results = {}
    for r, lotto in results:
        if lotto.code:
            code_results[lotto.code] = {
                "top_3": r.top_3 or (r.reward_data.get("top") if r.reward_data else ""), 
                "bottom_2": r.bottom_2 or (r.reward_data.get("bottom") if r.reward_data else ""),
                "created_at": r.created_at
            }
            
    # 3. จ่ายผลรางวัลกลับไปให้ "ทุกร้าน" ที่มีรหัสหวยตรงกัน (ใส่ร้านเดียว เห็นทุกร้าน!)
    all_lottos = db.query(LottoType).all()
    final_map = {}
    for lotto in all_lottos:
        if lotto.code in code_results:
            final_map[str(lotto.id)] = code_results[lotto.code]
            
    return final_map


@router.get("/history")
def get_reward_history(
    lotto_type_id: UUID,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # 1. หา Code ของหวยตัวนี้
    target_lotto = db.query(LottoType).get(lotto_type_id)
    if not target_lotto or not target_lotto.code:
        return []
        
    # 2. หา ID ของหวยชนิดนี้จาก "ทุกร้าน"
    related_lottos = db.query(LottoType).filter(LottoType.code == target_lotto.code).all()
    related_ids = [l.id for l in related_lottos]
    
    # 3. ดึงประวัติย้อนหลังและกรองไม่ให้วันที่ซ้ำกัน
    results = db.query(LottoResult).filter(
        LottoResult.lotto_type_id.in_(related_ids)
    ).order_by(LottoResult.round_date.desc()).all()
    
    unique_results = {}
    for r in results:
        if r.round_date not in unique_results:
            unique_results[r.round_date] = {
                "round_date": r.round_date,
                "top_3": r.top_3 or (r.reward_data.get("top") if r.reward_data else ""),
                "bottom_2": r.bottom_2 or (r.reward_data.get("bottom") if r.reward_data else "")
            }
    
    return list(unique_results.values())[:limit]