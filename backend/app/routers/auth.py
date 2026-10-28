"""Router: auth."""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import log_audit
from app.db import get_db
from app.models import AuthCredential, InstitutionMembership, Profile
from app.schemas import LoginRequest, MeResponse, TokenResponse
from app.security import CurrentUser, get_current_user, issue_session, require_roles, revoke_session, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _request_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    profile = db.scalars(select(Profile).where(Profile.email == email)).first()
    if profile is None or not profile.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    credential = db.get(AuthCredential, profile.id)
    if credential is None or not verify_password(payload.password, credential.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    profile.last_login_at = datetime.now(timezone.utc)
    token, session = issue_session(db, profile, _request_ip(request), request.headers.get("User-Agent"))
    log_audit(
        db,
        action="auth.login",
        entity_type="profile",
        entity_id=str(profile.id),
        meta={"role": profile.role},
        ip=_request_ip(request),
        actor=profile.id,
    )
    return TokenResponse(
        access_token=token,
        expires_at=session.expires_at,
        user=MeResponse.model_validate(profile),
    )


@router.post("/logout")
def logout(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    revoke_session(db, user.session_id)
    log_audit(db, action="auth.logout", entity_type="profile", entity_id=str(user.id), actor=user)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser = Depends(get_current_user)):
    return MeResponse.model_validate(user.profile)


@router.get("/me/institutions")
def my_institutions(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(InstitutionMembership.institution_id).where(InstitutionMembership.user_id == user.id)
    ).all()
    return {"institution_ids": [str(r) for r in rows]}


@router.get("/users", dependencies=[Depends(require_roles("admin", "reviewer", "auditor"))])
def list_profiles(db: Session = Depends(get_db)):
    rows = db.scalars(select(Profile).order_by(Profile.created_at)).all()
    return {
        "users": [
            {
                "id": str(p.id),
                "email": p.email,
                "full_name": p.full_name,
                "role": p.role,
                "is_active": p.is_active,
                "is_demo_account": p.is_demo_account,
            }
            for p in rows
        ]
    }


@router.patch("/users/{user_id}")
def update_user(
    user_id: uuid.UUID,
    payload: dict,
    admin: CurrentUser = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    profile = db.get(Profile, user_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    allowed_roles = {"admin", "reviewer", "institution", "auditor"}
    for key in ("role", "is_active"):
        if key in payload:
            if key == "role" and (payload["role"] not in allowed_roles or profile.id == admin.id):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Role change rejected (self-change or invalid role)")
            setattr(profile, key, payload[key])
    log_audit(
        db,
        action="admin.user_update",
        entity_type="profile",
        entity_id=str(profile.id),
        meta={"changes": {k: payload[k] for k in ("role", "is_active") if k in payload}},
        actor=admin,
    )
    return {"ok": True, "id": str(profile.id)}
