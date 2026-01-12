from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, field_validator
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
    line_channel_token: Optional[str] = None
    line_target_id: Optional[str] = None

    class Config:
        from_attributes = True

# 2. [เพิ่มใหม่] Schema สำหรับรับค่าแก้ไขการตั้งค่า
class ShopConfigUpdate(BaseModel):
    line_channel_token: Optional[str] = None
    line_target_id: Optional[str] = None

# --- User Schemas ---
class UserBase(BaseModel):
    username: str
    full_name: Optional[str] = None
    role: UserRole = UserRole.member

class UserCreate(UserBase):
    password: str
    shop_id: Optional[UUID] = None

class UserUpdate(BaseModel):
    username: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None

class UserResponse(BaseModel):
    id: UUID
    username: str
    full_name: Optional[str] = None
    role: UserRole
    shop_id: Optional[UUID]
    is_active: bool
    created_at: datetime
    credit_balance: Decimal
    shop_name: Optional[str] = None  # เพื่อรองรับ API /me ที่ส่งชื่อร้านกลับมาด้วย

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
        if v <= 1: raise ValueError("ยอดแทงต้องมากกว่า 1")
        return v

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
            raise ValueError('ผลรางวัล 3 ตัวบนต้องมี 3 หลัก')
        return v

    @field_validator('bottom_2')
    def validate_bottom(cls, v):
        if len(v) != 2 or not v.isdigit():
            raise ValueError('ผลรางวัล 2 ตัวล่างต้องมี 2 หลัก')
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
        if v == 0: raise ValueError("จำนวนเงินต้องไม่เป็น 0")
        return v
    
# --- Rate Profile Schemas ---
class RateProfileCreate(BaseModel):
    name: str
    rates: Dict[str, Any]

class RateProfileResponse(BaseModel):
    id: UUID
    name: str
    rates: Dict[str, Any]
    class Config:
        from_attributes = True

# --- Lotto Schemas ---
class LottoCreate(BaseModel):
    name: str
    code: str
    category: str = "GENERAL"
    rate_profile_id: Optional[UUID] = None
    img_url: Optional[str] = None
    open_time: Optional[str] = None   
    close_time: str
    result_time: Optional[str] = None
    api_link: Optional[str] = None
    open_days: List[str] = []
    is_template: bool = False

class LottoResponse(LottoCreate):
    id: UUID
    is_active: bool
    open_time: Optional[time] = None
    close_time: Optional[time] = None
    result_time: Optional[time] = None
    
    class Config:
        from_attributes = True

# --- Risk Management Schemas ---
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
    details: Optional[Dict[str, Any]]
    ip_address: Optional[str]
    created_at: datetime
    user_agent: Optional[str]
    user_id: UUID
    username: Optional[str] = None
    shop_name: Optional[str] = None
    
    class Config:
        from_attributes = True


# --- Bank Account ---
class BankAccountCreate(BaseModel):
    bank_name: str
    account_name: str
    account_number: str

class BankAccountResponse(BankAccountCreate):
    id: UUID
    is_active: bool
    class Config:
        from_attributes = True

# --- Top-up Request ---
class TopupCreate(BaseModel):
    amount: Decimal
    proof_image: Optional[str] = None # URL รูปสลิป

    @field_validator('amount')
    def validate_amount(cls, v):
        if v <= 0: raise ValueError("ยอดเงินต้องมากกว่า 0")
        return v

class TopupAction(BaseModel):
    status: str # "APPROVED" หรือ "REJECTED"
    remark: Optional[str] = None

class TopupResponse(BaseModel):
    id: UUID
    amount: Decimal
    proof_image: Optional[str]
    status: str
    created_at: datetime
    user_id: UUID
    username: Optional[str] = None # เอาไว้โชว์ชื่อคนแจ้ง
    
    class Config:
        from_attributes = True

class WithdrawCreate(BaseModel):
    amount: Decimal
    bank_name: str
    account_name: str
    account_number: str

    @field_validator('amount')
    def validate_amount(cls, v):
        if v <= 0: raise ValueError("ยอดเงินต้องมากกว่า 0")
        return v

class WithdrawResponse(WithdrawCreate):
    id: UUID
    status: str
    created_at: datetime
    admin_remark: Optional[str]
    
    class Config:
        from_attributes = True