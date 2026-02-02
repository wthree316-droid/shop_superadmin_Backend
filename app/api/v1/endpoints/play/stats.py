from decimal import Decimal
from typing import Optional
from datetime import datetime, time, date, timedelta
from sqlalchemy.orm import Session, joinedload
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, desc, extract

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
    ).filter(*base_filters, Ticket.status != TicketStatus.CANCELLED) 
    
    sales_result = sales_query.first()
    total_sales = sales_result.total_sales or 0
    total_tickets = sales_result.total_tickets or 0

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
    
    profit = total_sales - total_payout - total_pending

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_sales": total_sales,
        "total_tickets": total_tickets,
        "total_payout": total_payout,
        "total_pending": total_pending, 
        "total_cancelled": cancelled_count,
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