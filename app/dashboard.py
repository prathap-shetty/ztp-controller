"""Authenticated operator dashboard."""

import time
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dashboard_auth import authenticated, login_page, require_dashboard
from app.errors import ZtpError
from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.persistence.models import Attempt, AttemptEvent, RegistrationFailure, ValidationJob
from app.reprovision import prepare_reprovision

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not authenticated(request):
        return login_page()
    return HTMLResponse(
        Path(__file__).with_name("dashboard.html").read_text(),
        headers={"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"},
    )


@router.get("/api/dashboard")
def dashboard_data(request: Request):
    require_dashboard(request)
    state = request.app.state
    with Session(state.db) as session:
        failures = [
            {
                "serial": f.serial,
                "stage": f.stage,
                "code": f.code,
                "message": f.message,
                "at": f.created_at,
            }
            for f in session.scalars(
                select(RegistrationFailure)
                .order_by(RegistrationFailure.created_at.desc(), RegistrationFailure.id.desc())
                .limit(100)
            )
        ]
        attempts = session.scalars(
            select(Attempt).order_by(Attempt.created_at.desc()).limit(500)
        ).all()
        events = session.scalars(
            select(AttemptEvent)
            .where(AttemptEvent.attempt_id.in_([a.id for a in attempts]))
            .order_by(AttemptEvent.created_at, AttemptEvent.id)
        ).all()
        jobs = {
            job.attempt_id: job
            for job in session.scalars(
                select(ValidationJob).where(ValidationJob.attempt_id.in_([a.id for a in attempts]))
            )
        }
        grouped = {}
        for event in events:
            grouped.setdefault(event.attempt_id, []).append(
                {"kind": event.kind, "at": event.created_at}
            )
        rows = []
        for a in attempts:
            # Explicit allowlist: never return config bodies, tokens or raw inventory context.
            rows.append(
                {
                    "id": a.id,
                    "device_id": a.intent["device"]["id"],
                    "name": a.intent["device"]["name"],
                    "serial": a.observed["serial_number"],
                    "model": a.observed["model"],
                    "address": a.intent["management"]["address"],
                    "source": a.observed["current_version"],
                    "platform": a.observed["platform"],
                    "target": (a.manifest.get("target") or {}).get(
                        "target_version", "Configuration only"
                    ),
                    "state": a.state,
                    "archived": a.failure_reason == "operator_reprovision",
                    "reason": a.failure_reason,
                    "created_at": a.created_at,
                    "validation": a.manifest.get("validation_policy", "ssh"),
                    "upgrade_required": a.manifest["upgrade_required"],
                    "events": grouped.get(a.id, []),
                    "verification": verification_summary(jobs.get(a.id)),
                }
            )
    inventory, inventory_error = [], None
    if isinstance(state.inventory, LocalYamlInventoryProvider):
        try:
            for entry in state.inventory.list_entries():
                inventory.append(
                    {
                        "name": entry.device.name,
                        "serial": entry.device.serial_number,
                        "model": entry.device.model,
                        "address": entry.management.address,
                        "target": entry.software.target_version
                        if entry.software
                        else "Configuration only",
                        "enabled": entry.ztp_enabled,
                    }
                )
        except ZtpError:
            inventory_error = "Local inventory could not be loaded. Check its path and YAML format."
    return {
        "can_reprovision": state.config.lite_mode,
        "can_verify": state.config.lite_mode and state.config.lite_verification,
        "provider": state.config.inventory_provider,
        "mode": state.config.execution_mode,
        "attempts": rows,
        "failures": failures,
        "inventory": inventory,
        "inventory_error": inventory_error,
        "limit": 500,
    }


def verification_summary(job):
    if job is None:
        return None
    evidence = job.evidence or {}
    # Allowlist: show-command output and configuration bodies are never returned.
    return {
        "done": job.done,
        "running": job.lease_until > int(time.time()),
        "attempts": job.attempts,
        "next_at": None if job.done else job.available_at,
        "reason": evidence.get("reason"),
        "checked_at": evidence.get("checked_at"),
        "checks": evidence.get("checks"),
        "host_key": evidence.get("host_key"),
        "missing_lines": evidence.get("missing_lines") or [],
    }


@router.post("/api/dashboard/attempts/{attempt_id}/verify")
def verify_attempt(attempt_id: UUID, request: Request):
    require_dashboard(request)
    config = request.app.state.config
    if not (config.lite_mode and config.lite_verification):
        raise HTTPException(403, "Enable ZTP_LITE_VERIFICATION to verify devices")
    if request.headers.get("X-ZTP-Action") != "verify":
        raise HTTPException(403, "Dashboard action header required")
    now = int(time.time())
    with Session(request.app.state.db) as session, session.begin():
        attempt = session.scalar(
            select(Attempt).where(Attempt.id == str(attempt_id)).with_for_update()
        )
        if attempt is None or attempt.failure_reason == "operator_reprovision":
            raise HTTPException(404, "Attempt not found")
        if attempt.state == "VALIDATED":
            raise HTTPException(409, "Device is already verified")
        if attempt.state not in {"CONFIGURING", "VALIDATING"} or attempt.config_body is None:
            raise HTTPException(409, "Device has not reported configuration yet")
        job = session.scalar(
            select(ValidationJob).where(ValidationJob.attempt_id == attempt.id).with_for_update()
        )
        if job is None:
            session.add(
                ValidationJob(
                    attempt_id=attempt.id,
                    available_at=now,
                    deadline=now + config.validation_deadline_seconds,
                    attempts=0,
                    lease_until=0,
                    done=False,
                )
            )
        elif job.lease_until > now:
            raise HTTPException(409, "Verification is already running")
        else:
            # Restart the bounded retry budget; any pinned host key is kept.
            job.done, job.available_at, job.attempts = False, now, 0
            job.deadline = now + config.validation_deadline_seconds
    return {"message": "Verification queued. The worker checks ping, SSH, identity and config."}


class ReprovisionRequest(BaseModel):
    serial: str = Field(min_length=1, max_length=128)
    confirm_erased: StrictBool


@router.post("/api/dashboard/attempts/{attempt_id}/reprovision")
def reset_attempt(attempt_id: UUID, body: ReprovisionRequest, request: Request):
    require_dashboard(request)
    if not request.app.state.config.lite_mode:
        raise HTTPException(403, "UI reprovisioning requires Lite mode")
    # Cross-origin forms cannot set this header; cross-origin fetch must pass CORS,
    # which the dashboard intentionally does not enable.
    if request.headers.get("X-ZTP-Action") != "reprovision":
        raise HTTPException(403, "Dashboard action header required")
    if not body.confirm_erased:
        raise HTTPException(400, "Confirm the previous run is stopped and the switch is erased")
    try:
        archived = prepare_reprovision(request.app.state.db, body.serial, str(attempt_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        "archived_attempt_id": archived,
        "message": "Reset complete. Next POAP registration will create a fresh attempt.",
    }
