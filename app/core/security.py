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
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

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