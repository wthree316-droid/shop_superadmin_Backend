from decimal import Decimal
from typing import Optional
from datetime import datetime, time, date, timedelta
from sqlalchemy.orm import Session, joinedload
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, desc, extract, case

from app.api import deps
from app.db.session import get_db
from app.models.lotto import Ticket, TicketItem, TicketStatus
from app.models.user import User, UserRole

router = APIRouter()

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

    start_utc = datetime.combine(s_date, time.min) - timedelta(hours=7)
    end_utc = datetime.combine(e_date, time.max) - timedelta(hours=7)

    base_filters = [
        Ticket.created_at >= start_utc,
        Ticket.created_at <= end_utc
    ]
    if current_user.role == UserRole.admin:
        base_filters.append(Ticket.shop_id == current_user.shop_id)

    sales_query = db.query(
        func.sum(Ticket.total_amount).label("total_sales"),
        func.count(Ticket.id).label("total_tickets"),
        func.sum(Ticket.commission_amount).label("total_commission")
    ).filter(*base_filters, Ticket.status != TicketStatus.CANCELLED) 
    
    sales_result = sales_query.first()
    total_sales = sales_result.total_sales or 0
    total_tickets = sales_result.total_tickets or 0
    total_commission = sales_result.total_commission or 0

    payout_query = db.query(func.sum(TicketItem.winning_amount))\
        .join(Ticket)\
        .filter(*base_filters)\
        .filter(Ticket.status != TicketStatus.CANCELLED)\
        .filter(TicketItem.status == 'WIN')
    total_payout = payout_query.scalar() or 0

    pending_query = db.query(func.sum(Ticket.total_amount))\
        .filter(*base_filters)\
        .filter(Ticket.status == TicketStatus.PENDING)
    total_pending = pending_query.scalar() or 0

    cancelled_count = db.query(func.count(Ticket.id))\
        .filter(*base_filters, Ticket.status == TicketStatus.CANCELLED)\
        .scalar() or 0
    
    profit = total_sales - total_payout - total_pending - total_commission

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_sales": total_sales,
        "total_tickets": total_tickets,
        "total_payout": total_payout,
        "total_pending": total_pending, 
        "total_cancelled": cancelled_count,
        "total_commission": total_commission,
        "profit": profit
    }

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

@router.get("/stats/top_numbers")
def get_top_numbers(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 200,
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
        
    results = query.group_by(TicketItem.number).order_by(desc("total_amount")).limit(limit).all()
        
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

    # 1. กำหนด Filter พื้นฐาน
    base_filters = [
        Ticket.created_at >= start_utc,
        Ticket.created_at <= end_utc
    ]
    if current_user.role == UserRole.admin:
        base_filters.append(Ticket.shop_id == current_user.shop_id)

    # 🚀 2. ให้ Database ทำการบวกเลขและจัดกลุ่มให้เลย (ไวกว่า Python 1,000 เท่า!)
    ticket_stats = db.query(
        User.id.label("user_id"),
        User.username,
        User.full_name,
        User.role,
        User.commission_percent,
        func.count(Ticket.id).label("bill_count"),
        func.sum(case([(Ticket.status != TicketStatus.CANCELLED, Ticket.total_amount)], else_=0)).label("total_bet"),
        func.sum(case([(Ticket.status == TicketStatus.CANCELLED, Ticket.total_amount)], else_=0)).label("cancelled_amount"),
        func.sum(case([(Ticket.status == TicketStatus.PENDING, Ticket.total_amount)], else_=0)).label("pending_amount"),
        func.sum(case([(Ticket.status != TicketStatus.CANCELLED, Ticket.commission_amount)], else_=0)).label("total_commission")
    ).join(Ticket, User.id == Ticket.user_id).filter(*base_filters).group_by(
        User.id, User.username, User.full_name, User.role, User.commission_percent
    ).all()

    # 🚀 3. Query แยกสำหรับหายอดถูกรางวัล (Winning Amount)
    win_stats = db.query(
        Ticket.user_id,
        func.sum(TicketItem.winning_amount).label("total_win")
    ).join(Ticket, Ticket.id == TicketItem.ticket_id).filter(
        *base_filters,
        Ticket.status == TicketStatus.WIN,
        TicketItem.status == 'WIN'
    ).group_by(Ticket.user_id).all()

    # แปลงข้อมูลถูกรางวัลเป็น Dictionary เพื่อให้ค้นหาได้ไวที่สุด O(1)
    win_map = {str(w.user_id): w.total_win or Decimal(0) for w in win_stats}

    # 4. จัดรูปฟอร์แมตเพื่อส่งกลับไปให้หน้าเว็บ
    results = []
    for t in ticket_stats:
        uid_str = str(t.user_id)
        results.append({
            "user_id": uid_str,
            "username": t.username,
            "full_name": t.full_name or "-",
            "role": t.role.value,
            "total_bet": t.total_bet or Decimal(0),
            "total_win": win_map.get(uid_str, Decimal(0)),
            "pending_amount": t.pending_amount or Decimal(0),
            "cancelled_amount": t.cancelled_amount or Decimal(0),
            "total_commission": t.total_commission or Decimal(0),
            "commission_percent": float(t.commission_percent or 0),
            "bill_count": t.bill_count or 0
        })

    # เรียงลำดับคนแทงเยอะสุดขึ้นก่อน
    results.sort(key=lambda x: x["total_bet"], reverse=True)
    
    return results