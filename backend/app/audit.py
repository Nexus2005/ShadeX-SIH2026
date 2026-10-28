"""Append-only audit trail via public.log_audit_event (hash chain maintained in DB)."""
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.security import CurrentUser


def _actor_id(actor) -> str | None:
    if actor is None:
        return None
    if isinstance(actor, uuid.UUID):
        return str(actor)
    return str(getattr(actor, "id", actor))


def log_audit(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    institution_id: uuid.UUID | None = None,
    meta: dict | None = None,
    request_id: str | None = None,
    ip: str | None = None,
    actor: CurrentUser | None = None,
) -> uuid.UUID:
    event_id = db.execute(
        text(
            "select public.log_audit_event("
            " :action, :entity_type, :entity_id, :application_id, :institution_id,"
            " cast(:meta as jsonb), :request_id, :ip, :actor_id)"
        ),
        {
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id else None,
            "application_id": application_id,
            "institution_id": institution_id,
            "meta": _json(meta or {}),
            "request_id": request_id,
            "ip": ip,
            "actor_id": _actor_id(actor),
        },
    ).scalar_one()
    db.flush()
    return event_id


def _json(value: dict) -> str:
    import json

    return json.dumps(value, default=str, ensure_ascii=False)
