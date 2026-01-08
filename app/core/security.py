import jwt
from datetime import datetime, timedelta, timezone
from typing import Any, Union, Optional
from passlib.context import CryptContext
from app.core.config import settings

# ใช้ bcrypt สำหรับ hashing รหัสผ่าน
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(subject: Union[str, Any], role: str, expires_delta: Optional[timedelta] = None) -> str:
    """
    สร้าง JWT Token โดยใช้ PyJWT
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    # ข้อมูลที่จะเก็บใน Token
    to_encode = {
        "exp": expire, 
        "sub": str(subject),
        "role": role
    }
    
    # สร้าง Token
    encoded_jwt = jwt.encode(
        to_encode, 
        settings.SECRET_KEY, 
        algorithm=settings.ALGORITHM
    )
    return encoded_jwt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    ตรวจสอบรหัสผ่าน โดยป้องกันปัญหา bcrypt 72-character limit
    """
    try:
        # Bcrypt มีข้อจำกัดที่ 72 bytes หากยาวกว่านั้นจะตัดทิ้ง
        # เราตัดเองก่อนเพื่อป้องกัน passlib พ่น ValueError
        return pwd_context.verify(plain_password[:72], hashed_password)
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    """
    สร้าง Hash จากรหัสผ่าน (จำกัด 72 ตัวอักษร)
    """
    return pwd_context.hash(password[:72])

def decode_token(token: str) -> Optional[dict]:
    """
    ถอดรหัสและตรวจสอบ Token
    """
    try:
        payload = jwt.decode(
            token, 
            settings.SECRET_KEY, 
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except (jwt.PyJWTError, Exception):
        return None