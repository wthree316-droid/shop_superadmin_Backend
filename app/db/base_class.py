from typing import Any
from sqlalchemy.ext.declarative import as_declarative, declared_attr

@as_declarative()
class Base:
    id: Any
    __name__: str

    # ฟังก์ชันนี้ช่วยแปลงชื่อ Class เป็นชื่อ Table อัตโนมัติ (เช่น User -> users)
    # แต่ในโปรเจกต์นี้เรากำหนด __tablename__ เองอยู่แล้ว ก็ใส่ไว้เป็นมาตรฐานครับ
    @declared_attr
    def __tablename__(cls) -> str:
        return cls.__name__.lower()
    