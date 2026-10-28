"""Authentication & authorization: bcrypt credentials, JWT session tokens, RBAC deps."""
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionFactory, get_db
from app.models import Profile, UserSession

settings = get_settings()

Role = Literal["admin", "reviewer", "institution", "auditor"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


class JWTSubEncoder(json.JSONEncoder):
    def default(self, o):  # noqa: ANN001
        if isinstance(o, uuid.UUID):
            return str(o)
        return super().default(o)


def issue_session(db: Session, user: Profile, ip: str | None, user_agent: str | None) -> tuple[str, UserSession]:
    now = datetime.now(timezone.utc)
    session = UserSession(
        user_id=user.id,
        expires_at=now + timedelta(minutes=settings.SESSION_TTL_MINUTES),
        ip_address=ip,
        user_agent=user_agent[:400] if user_agent else None,
    )
    db.add(session)
    db.flush()
    token = jwt.encode(
        {
            "sub": str(user.id),
            "sid": str(session.id),
            "role": user.role,
            "iat": int(now.timestamp()),
            "exp": int(session.expires_at.timestamp()),
            "jti": secrets.token_hex(12),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    return token, session


def revoke_session(db: Session, session_id: uuid.UUID) -> None:
    session = db.get(UserSession, session_id)
    if session and not session.revoked_at:
        session.revoked_at = datetime.now(timezone.utc)


@dataclass
class CurrentUser:
    profile: Profile
    session_id: uuid.UUID

    @property
    def id(self) -> uuid.UUID:
        return self.profile.id

    @property
    def role(self) -> str:
        return self.profile.role

    def is_staff(self) -> bool:
        return self.profile.role in ("admin", "reviewer", "auditor")

    def is_admin(self) -> bool:
        return self.profile.role == "admin"

    def is_reviewer_or_admin(self) -> bool:
        return self.profile.role in ("admin", "reviewer")


def get_auth_user(request: Request, db_request: "Session | None" = None) -> CurrentUser:
    """Verify Bearer token, session validity and profile state."""
    auth_header = request.headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = auth_header.removeprefix("Bearer ").strip()
    try:
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired; sign in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session token")

    user_id = claims.get("sub")
    sid = claims.get("sid")
    if not user_id or not sid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token")

    from app.models import AuthCredential  # noqa: F401 (import guard)

    db = SessionFactory() if db_request is None else db_request
    try:
        session = db.get(UserSession, uuid.UUID(sid))
        now = datetime.now(timezone.utc)
        if session is None or session.revoked_at is not None or session.expires_at < now:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session no longer valid")
        profile = db.get(Profile, uuid.UUID(user_id))
        if profile is None or not profile.is_active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is inactive")
        return CurrentUser(profile=profile, session_id=session.id)
    finally:
        if db_request is None:
            db.close()


def get_current_user(
    db: Session = Depends(get_db),
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    claims = _decode_token(authorization.removeprefix("Bearer ").strip())
    user_id, sid = claims.get("sub"), claims.get("sid")
    session = db.get(UserSession, uuid.UUID(sid))
    now = datetime.now(timezone.utc)
    if session is None or session.revoked_at is not None or session.expires_at < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session no longer valid")
    profile = db.get(Profile, uuid.UUID(user_id))
    if profile is None or not profile.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is inactive")
    return CurrentUser(profile=profile, session_id=session.id)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired; sign in again") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session token") from e


def require_roles(*roles: Role):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This action requires one of the roles: {', '.join(roles)}",
            )
        return user

    return dependency


def require_staff():
    return require_roles("admin", "reviewer", "auditor")


def require_reviewer_or_admin():
    return require_roles("admin", "reviewer")


def require_admin():
    return require_roles("admin")


def user_institution_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    from app.models import InstitutionMembership

    rows = db.scalars(
        select(InstitutionMembership.institution_id).where(InstitutionMembership.user_id == user_id)
    ).all()
    return set(rows)


def user_can_write_application(db: Session, user: CurrentUser, application) -> bool:
    if user.is_reviewer_or_admin():
        return True
    return application.institution_id in user_institution_ids(db, user.id)


def user_can_read_application(db: Session, user: CurrentUser, application) -> bool:
    return user.is_staff() or application.institution_id in user_institution_ids(db, user.id)


def db_exec(db: Session, sql: str, params: dict | None = None):
    return db.execute(text(sql), params or {})
