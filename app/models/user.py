import uuid
import enum
from sqlalchemy import Column, String, Boolean, ForeignKey, DECIMAL, Enum as SAEnum, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base

# Enum เอาไว้ที่นี่คู่กับ User
class UserRole(str, enum.Enum):
    superadmin = "superadmin"
    admin = "admin"
    member = "member"

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    
    role = Column(SAEnum(UserRole), nullable=False, default=UserRole.member)
    
    # เชื่อมไปที่ table shops
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id"), nullable=True, index=True)
    
    full_name = Column(String, nullable=True)
    credit_balance = Column(DECIMAL(15, 2), default=0.00)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    failed_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    # Relationship: เชื่อมกลับไปหา Shop
    shop = relationship("Shop", back_populates="users")