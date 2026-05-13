import os
import bcrypt
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError

_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
_ALGO = "HS256"
_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: int, username: str, role: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": str(user_id), "username": username, "role": role, "exp": exp},
        _SECRET,
        algorithm=_ALGO,
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALGO])
    except JWTError as e:
        raise ValueError(str(e))
