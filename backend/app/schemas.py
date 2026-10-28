"""Pydantic API schemas (v1)."""
import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- auth

class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class MeResponse(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: MeResponse


# ---------------------------------------------------------------- institutions

class InstitutionCreate(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    aicte_pid: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9-]{3,32}$")
    institution_type: str = "Engineering and Technology"
    trust_society_name: str | None = None
    address_line: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None
    established_year: int | None = None


class InstitutionUpdate(BaseModel):
    name: str | None = None
    institution_type: str | None = None
    trust_society_name: str | None = None
    address_line: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None
    established_year: int | None = None


# ---------------------------------------------------------------- applications

class ApplicationCreate(BaseModel):
    institution_id: uuid.UUID
    category_code: str
    academic_year: str = Field(pattern=r"^\d{4}-\d{2}$")
    title: str = Field(min_length=3, max_length=200)
    description: str | None = None
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    declared_data: dict[str, Any] = Field(default_factory=dict)


class ApplicationUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    declared_data: dict[str, Any] | None = None


class StatusChange(BaseModel):
    to_status: Literal[
        "submitted", "processing", "needs_correction", "under_review", "reviewed", "closed"
    ]
    reason: str | None = None


# ---------------------------------------------------------------- findings

class ReviewActionIn(BaseModel):
    action: Literal[
        "assign", "comment", "start_review", "confirm", "dismiss",
        "request_clarification", "resolve", "reopen", "institution_response",
        "application_decision",
    ]
    reason_code: str | None = None
    comment: str | None = None
    visibility_release: bool | None = None


class FieldCorrectionIn(BaseModel):
    new_value: str = Field(min_length=1, max_length=400)
    reason: str = Field(min_length=4, max_length=500)
