from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, EmailStr, field_validator
from enum import Enum
from datetime import datetime, time, date
from decimal import Decimal
from app.models.user import UserRole

# --- Shop Schemas ---
class ShopBase(BaseModel):
    name: str
    code: str

class ShopCreate(ShopBase):
    pass

class ShopResponse(ShopBase):
    id: UUID
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

# --- User Schemas ---
class UserBase(BaseModel):
    username: str
    full_name: Optional[str] = None
    role: UserRole = UserRole.member

class UserCreate(UserBase):
    password: str
    shop_id: Optional[UUID] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None

class UserResponse(UserBase):
    id: UUID
    shop_id: Optional[UUID]
    shop_name: Optional[str] = None
    is_active: bool
    created_at: datetime
    credit_balance: Decimal
    class Config:
        from_attributes = True

# --- Auth Schemas ---
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None

# --- Ticket Schemas ---
class BetItemCreate(BaseModel):
    number: str
    bet_type: str
    amount: Decimal
    
    @field_validator('amount')
    @classmethod
    def validate_amount(cls, v):
        if v <= 0: raise ValueError("Amount must be positive")
        return v

# [เพิ่ม] Schema สำหรับส่งรายการย่อยกลับไปหน้าบ้าน
class BetItemResponse(BaseModel):
    id: UUID
    number: str
    bet_type: str
    amount: Decimal
    reward_rate: Decimal
    winning_amount: Decimal
    status: str
    
    class Config:
        from_attributes = True

class TicketCreate(BaseModel):
    lotto_type_id: UUID
    items: List[BetItemCreate]
    note: Optional[str] = None
    shop_id: Optional[UUID] = None
    
class TicketUser(BaseModel):
    username: str
    full_name: Optional[str] = None
    class Config:
        from_attributes = True


class LottoResponseShort(BaseModel):
    name: str
    code: str
    img_url: Optional[str] = None

class TicketResponse(BaseModel):
    id: UUID
    total_amount: Decimal
    status: str
    created_at: datetime
    note: Optional[str] = None
    user: Optional[TicketUser] = None
    items: List[BetItemResponse] = []
    lotto_type: Optional[LottoResponseShort] = None
    class Config:
        from_attributes = True

# --- Reward Schemas ---
class RewardRequest(BaseModel):
    lotto_type_id: UUID
    top_3: str
    bottom_2: str
    
    @field_validator('top_3')
    def validate_top(cls, v):
        if len(v) != 3 or not v.isdigit():
            raise ValueError('Top 3 reward must be exactly 3 digits')
        return v

    @field_validator('bottom_2')
    def validate_bottom(cls, v):
        if len(v) != 2 or not v.isdigit():
            raise ValueError('Bottom 2 reward must be exactly 2 digits')
        return v

class RewardResultResponse(BaseModel):
    total_tickets_processed: int
    total_winners: int
    total_payout: Decimal

class RewardHistoryResponse(BaseModel):
    id: UUID
    lotto_name: str
    round_date: date
    top_3: Optional[str] = None
    bottom_2: Optional[str] = None
    
    class Config:
        from_attributes = True

# --- Admin Member Management ---
class MemberCreate(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None

class CreditAdjustment(BaseModel):
    amount: Decimal
    note: Optional[str] = None

    @field_validator('amount')
    def validate_amount(cls, v):
        if v == 0: raise ValueError("Amount cannot be zero")
        return v
    
# --- Rate Profile Schemas ---
class RateProfileCreate(BaseModel):
    name: str
    rates: dict

class RateProfileResponse(RateProfileCreate):
    id: UUID
    class Config:
        from_attributes = True

# --- Lotto Schemas (รวมเวอร์ชัน Full มาไว้ที่นี่เลย) ---
class LottoCreate(BaseModel):
    name: str
    code: str
    category: str = "GENERAL"
    rate_profile_id: Optional[UUID] = None
    
    # ฟิลด์ใหม่ (Full)
    img_url: Optional[str] = None
    open_time: Optional[str] = None   
    close_time: str
    result_time: Optional[str] = None
    api_link: Optional[str] = None
    open_days: List[str] = []

class LottoResponse(LottoCreate):
    id: UUID
    is_active: bool
    
    # แปลง Time object -> String อัตโนมัติด้วย Pydantic
    open_time: Optional[time] = None
    close_time: Optional[time] = None
    result_time: Optional[time] = None
    
    class Config:
        from_attributes = True

# --- Risk Management Schemas (ย้ายมาจาก play.py) ---
class NumberRiskCreate(BaseModel):
    lotto_type_id: UUID
    number: str
    risk_type: str # "CLOSE" หรือ "HALF"

class NumberRiskResponse(NumberRiskCreate):
    id: UUID
    class Config:
        from_attributes = True


# --- Audit Log Schemas ---
class AuditLogResponse(BaseModel):
    id: int
    action: str
    target_table: Optional[str]
    details: Optional[dict]
    ip_address: Optional[str]
    created_at: datetime
    user_agent: Optional[str]
    user_id: UUID
    
    class Config:
        from_attributes = True