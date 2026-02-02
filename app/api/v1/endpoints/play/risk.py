from typing import List, Optional
from datetime import datetime, time, timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.schemas import NumberRiskCreate, NumberRiskResponse, BulkRiskCreate
from app.db.session import get_db
from app.models.lotto import NumberRisk
from app.models.user import User, UserRole
from app.core.risk_cache import invalidate_cache

router = APIRouter()

@router.post("/risks/batch")
def create_bulk_risks(
    payload: BulkRiskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    count = 0
    risk_created_at = datetime.utcnow()
    if hasattr(payload, 'date') and payload.date:
        try:
            target_date = datetime.strptime(payload.date, "%Y-%m-%d").date()
            risk_created_at = datetime.combine(target_date, time.min) - timedelta(hours=7)
        except ValueError:
            pass

    try:
        for item in payload.items:
            start_of_day = risk_created_at
            end_of_day = risk_created_at + timedelta(days=1)

            existing = db.query(NumberRisk).filter(
                NumberRisk.lotto_type_id == payload.lotto_type_id,
                NumberRisk.number == item.number,
                NumberRisk.specific_bet_type == item.specific_bet_type,
                NumberRisk.created_at >= start_of_day,
                NumberRisk.created_at < end_of_day
            ).first()

            if not existing:
                new_risk = NumberRisk(
                    lotto_type_id=payload.lotto_type_id,
                    number=item.number,
                    specific_bet_type=item.specific_bet_type,
                    risk_type=payload.risk_type,
                    shop_id=current_user.shop_id,
                    created_at=risk_created_at
                )
                db.add(new_risk)
                count += 1
            else:
                existing.risk_type = payload.risk_type
        
        db.commit()
        invalidate_cache(str(payload.lotto_type_id))
        return {"message": "success", "inserted": count}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.delete("/risks/clear")
def clear_risks_by_date(
    lotto_id: UUID,
    date: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
        start_utc = datetime.combine(target_date, time.min) - timedelta(hours=7)
        end_utc = datetime.combine(target_date, time.max) - timedelta(hours=7)

        stmt = db.query(NumberRisk).filter(
            NumberRisk.lotto_type_id == lotto_id,
            NumberRisk.created_at >= start_utc,
            NumberRisk.created_at <= end_utc
        )
        if current_user.role == UserRole.admin:
            stmt = stmt.filter(NumberRisk.shop_id == current_user.shop_id)

        deleted_count = stmt.delete(synchronize_session=False)
        db.commit()
        invalidate_cache(str(lotto_id))
        return {"status": "success", "deleted": deleted_count}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error clearing risks: {str(e)}")

@router.get("/risks/daily/all")
def get_all_daily_risks(
    date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            target_date = (datetime.utcnow() + timedelta(hours=7)).date()
    else:
        target_date = (datetime.utcnow() + timedelta(hours=7)).date()

    start_utc = datetime.combine(target_date, time.min) - timedelta(hours=7)
    end_utc = datetime.combine(target_date, time.max) - timedelta(hours=7)

    query = db.query(NumberRisk).filter(
        NumberRisk.created_at >= start_utc,
        NumberRisk.created_at <= end_utc
    )

    if current_user.role != UserRole.superadmin:
        if current_user.shop_id:
            query = query.filter(NumberRisk.shop_id == current_user.shop_id)
        else:
            return {} 

    risks = query.all()
    grouped_risks = {}
    for r in risks:
        lid = str(r.lotto_type_id)
        if lid not in grouped_risks:
            grouped_risks[lid] = []
        
        grouped_risks[lid].append({
            "number": r.number,
            "risk_type": r.risk_type,
            "specific_bet_type": r.specific_bet_type
        })
    return grouped_risks

@router.get("/risks/{lotto_id}", response_model=List[NumberRiskResponse])
def get_risks(
    lotto_id: UUID,
    date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            target_date = (datetime.utcnow() + timedelta(hours=7)).date()
    else:
        target_date = (datetime.utcnow() + timedelta(hours=7)).date()

    start_utc = datetime.combine(target_date, time.min) - timedelta(hours=7)
    end_utc = datetime.combine(target_date, time.max) - timedelta(hours=7)

    return db.query(NumberRisk).filter(
        NumberRisk.lotto_type_id == lotto_id,
        NumberRisk.created_at >= start_utc,
        NumberRisk.created_at <= end_utc
    ).all()

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