import uuid
import enum
from sqlalchemy import Column, String, Boolean, ForeignKey, DECIMAL, DateTime, Time, JSON, Text, Date, UniqueConstraint, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base

# Enum สถานะโพย
class TicketStatus(str, enum.Enum):
    PENDING = "PENDING"
    WIN = "WIN"
    LOSE = "LOSE"
    CANCELLED = "CANCELLED"

# [เพิ่ม] ตารางเก็บแม่แบบเรทจ่าย (Rate Templates)
class RateProfile(Base):
    __tablename__ = "rate_profiles"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False) # ชื่อเรท เช่น "เรทมาตรฐาน", "เรท VIP"
    rates = Column(JSON, default={})      # เก็บ JSON { "2up": 90, "3top": 900 }
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id"), nullable=True) 
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    # Relationship
    lottos = relationship("LottoType", back_populates="rate_profile")

class LottoType(Base):
    __tablename__ = "lotto_types"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    code = Column(String(20), unique=True, nullable=False)
    category = Column(String, default="GENERAL")
    
    # --- [ชุดเวลา] ---
    open_time = Column(String, nullable=True)    # [ใหม่] เวลาเปิด
    close_time = Column(String, nullable=True)   # เวลาปิด (มีอยู่แล้ว)
    result_time = Column(String, nullable=True)  # [ใหม่] เวลาผลออก
    
    # --- [การตั้งค่าอื่นๆ] ---
    is_active = Column(Boolean, default=True)
    img_url = Column(String, nullable=True)     # [ใหม่] รูปภาพ
    api_link = Column(String, nullable=True)    # [ใหม่] API Link
    open_days = Column(JSON, default=[])        # [ใหม่] วันที่เปิดรับ ["MON", "TUE"]
    
    #  เพื่อระบุเจ้าของหวย
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id"), nullable=True) 
    
    #  ถ้าเป็น True คือแม่แบบ (SuperAdmin สร้าง) ถ้า False คือหวยจริงที่เปิดเล่น
    is_template = Column(Boolean, default=False) 
    
    # --- [Relationships] ---
    rate_profile_id = Column(UUID(as_uuid=True), ForeignKey("rate_profiles.id"), nullable=True)
    rate_profile = relationship("RateProfile", back_populates="lottos")
    rules = Column(JSON, default={})

    # Relationship
    shop = relationship("Shop", backref="lottos")

class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    lotto_type_id = Column(UUID(as_uuid=True), ForeignKey("lotto_types.id"))
    note = Column(String, nullable=True)
    total_amount = Column(DECIMAL(10, 2), nullable=False)
    status = Column(String, default=TicketStatus.PENDING)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    round_date = Column(Date, nullable=True)
    
    # Relationships
    items = relationship("TicketItem", back_populates="ticket", cascade="all, delete-orphan")
    user = relationship("User", backref="tickets") # เพื่อให้เรียก user.tickets ได้
    lotto_type = relationship("LottoType")
    shop = relationship("Shop")

class TicketItem(Base):
    __tablename__ = "ticket_items"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False)
    
    number = Column(String, nullable=False)
    bet_type = Column(String, nullable=False) # 2up, 3tod, etc.
    amount = Column(DECIMAL(10, 2), nullable=False)
    reward_rate = Column(DECIMAL(10, 2), nullable=False)
    winning_amount = Column(DECIMAL(10, 2), default=0.00)
    status = Column(String, default=TicketStatus.PENDING)
    
    ticket = relationship("Ticket", back_populates="items")


class LottoResult(Base):
    __tablename__ = "lotto_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lotto_type_id = Column(UUID(as_uuid=True), ForeignKey("lotto_types.id"), nullable=False)
    round_date = Column(Date, nullable=False)
    
    # ✅ [เพิ่ม] คอลัมน์สำหรับเก็บเลข 3 ตัวบน และ 2 ตัวล่าง (Reward.py เรียกใช้ตัวนี้)
    top_3 = Column(String, nullable=True)
    bottom_2 = Column(String, nullable=True)
    
    reward_data = Column(JSON, nullable=False) # เก็บ JSON รวม (เผื่ออนาคต)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    lotto_type = relationship("LottoType")

    __table_args__ = (
        UniqueConstraint('lotto_type_id', 'round_date', name='unique_result_per_round'),
    )


# [เพิ่ม] ตารางเก็บเลขอั้น/เลขปิด
class NumberRisk(Base):
    __tablename__ = "number_risks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lotto_type_id = Column(UUID(as_uuid=True), ForeignKey("lotto_types.id"), nullable=False)
    shop_id = Column(UUID(as_uuid=True), nullable=True)
    number = Column(String, nullable=False)  # เลขที่อั้น เช่น "59", "123"
    risk_type = Column(String, nullable=False) # CLOSE=ปิดรับ, HALF=จ่ายครึ่ง
    specific_bet_type = Column(String, default="ALL") # แก้ type hint เป็น Column ปกติ
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationship กลับไปหาหวย (Optional)
    lotto = relationship("LottoType")

class LottoCategory(Base):
    __tablename__ = "lotto_categories"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label = Column(String, nullable=False) # ชื่อหมวด เช่น "หวยหุ้นวีไอพี"
    color = Column(String, default="bg-gray-100 text-gray-700") # สีปุ่ม (Tailwind Class)
    shop_id = Column(UUID(as_uuid=True), ForeignKey("shops.id"), nullable=True) # ผูกกับร้านค้า
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    order_index = Column(Integer, default=10)