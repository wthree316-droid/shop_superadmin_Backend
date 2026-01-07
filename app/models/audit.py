from sqlalchemy import Column, String, BigInteger, ForeignKey, DateTime, JSON, Text
from sqlalchemy.dialects.postgresql import UUID, INET
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id"))
    
    action = Column(String, nullable=False) # LOGIN, ADJUST_CREDIT
    target_id = Column(UUID(as_uuid=True), nullable=True)
    target_table = Column(String, nullable=True)
    
    details = Column(JSON, nullable=True)
    ip_address = Column(INET, nullable=True)
    user_agent = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User")
    shop = relationship("Shop")