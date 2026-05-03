from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import Settings, get_settings


security_scheme = HTTPBearer()
ALGORITHM = "HS256"


def create_access_token(subject: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    payload = {"sub": subject, "exp": expires}
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def require_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    try:
        payload = jwt.decode(credentials.credentials, settings.app_secret_key, algorithms=[ALGORITHM])
        subject = payload.get("sub")
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    if subject != settings.app_superuser_username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")
    return subject
