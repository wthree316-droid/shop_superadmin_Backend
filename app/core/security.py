from datetime import datetime, timedelta
from typing import Optional
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings

# ใช้ bcrypt ในการ hash password (มาตรฐานความปลอดภัย)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ฟังก์ชันตรวจสอบรหัสผ่าน (รับ text ดิบ เทียบกับ hash ใน db)
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# ฟังก์ชันแปลงรหัสเป็น hash ก่อนลง DB
def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

# ฟังก์ชันสร้าง JWT Token
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt