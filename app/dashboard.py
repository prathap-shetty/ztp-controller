"""Authenticated read-only operator dashboard."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dashboard_auth import authenticated, login_page, require_dashboard
from app.errors import ZtpError
from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.persistence.models import Attempt, AttemptEvent, RegistrationFailure

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
                    "target": a.manifest["target"]["target_version"],
                    "state": a.state,
                    "archived": a.failure_reason == "operator_reprovision",
                    "reason": a.failure_reason,
                    "created_at": a.created_at,
                    "validation": a.manifest.get("validation_policy", "ssh"),
                    "upgrade_required": a.manifest["upgrade_required"],
                    "events": grouped.get(a.id, []),
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
                        "target": entry.software.target_version,
                        "enabled": entry.ztp_enabled,
                    }
                )
        except ZtpError:
            inventory_error = "Local inventory could not be loaded. Check its path and YAML format."
    return {
        "provider": state.config.inventory_provider,
        "mode": state.config.execution_mode,
        "attempts": rows,
        "failures": failures,
        "inventory": inventory,
        "inventory_error": inventory_error,
        "limit": 500,
    }
