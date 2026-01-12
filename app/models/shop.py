import uuid
from sqlalchemy import Column, String, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base_class import Base # เรียกใช้ Base ตัวใหม่

class Shop(Base):
    __tablename__ = "shops"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    code = Column(String(10), unique=True, nullable=False, index=True) # รหัสร้าน
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    line_channel_token = Column(String, nullable=True)
    line_target_id = Column(String, nullable=True)
    
    # Relationship: เชื่อมไปหา User (ใช้ string "User" เพื่อเลี่ยง circular import)
    users = relationship("User", back_populates="shop", cascade="all, delete-orphan")
    