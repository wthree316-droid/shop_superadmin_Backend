import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, DECIMAL, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base

# Model บัญชีธนาคาร (สำหรับเก็บลง Database)
class ShopBankAccount(Base):
    __tablename__ = "shop_bank_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id"), nullable=False)
    
    bank_name = Column(String(50), nullable=False)
    account_name = Column(String(100), nullable=False)
    account_number = Column(String(50), nullable=False)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    shop = relationship("Shop")

# Model รายการแจ้งเติมเงิน (สำหรับเก็บลง Database)
class TopupRequest(Base):
    __tablename__ = "topup_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    amount = Column(DECIMAL(15, 2), nullable=False)
    proof_image = Column(String, nullable=True) # URL รูปสลิป
    
    status = Column(String(20), default='PENDING') # PENDING, APPROVED, REJECTED
    
    admin_remark = Column(Text, nullable=True)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationship
    shop = relationship("Shop")
    user = relationship("User", foreign_keys=[user_id])
    approver = relationship("User", foreign_keys=[approved_by])