import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ZtpError
from app.models.contracts import DeviceEvent
from app.persistence.models import Attempt, AttemptEvent, ValidationJob


def add_event(session, attempt, kind, event_id=None):
    session.add(
        AttemptEvent(
            id=event_id or str(uuid.uuid4()),
            attempt_id=attempt.id,
            kind=kind,
            created_at=int(time.time()),
        )
    )


def device_event(engine, attempt_id: str, event: DeviceEvent):
    with Session(engine) as session, session.begin():
        attempt = session.scalar(select(Attempt).where(Attempt.id == attempt_id).with_for_update())
        if (
            not attempt
            or attempt.manifest["mode"] != "configuration-only"
            or event.config_sha256 != attempt.manifest["config"]["sha256"]
        ):
            raise ZtpError("invalid_event", 409, "Event does not match the configuration attempt")
        previous = session.get(AttemptEvent, event.event_id)
        if previous:
            if previous.attempt_id != attempt_id or previous.kind != event.event:
                raise ZtpError("event_conflict", 409, "Event ID already used")
            return {"state": attempt.state, "duplicate": True}
        if attempt.state in {"FAILED", "VALIDATED"}:
            raise ZtpError("terminal_attempt", 409, "Attempt is terminal")
        if event.event == "CONFIG_STAGED":
            if attempt.state == "AUTHORIZED":
                attempt.state = "CONFIGURING"
        else:
            attempt.state, attempt.failure_reason = "FAILED", "bootstrap_failed"
        add_event(session, attempt, event.event, event.event_id)
        return {"state": attempt.state, "duplicate": False}


def claim_validation(engine, lease_seconds: int):
    now = int(time.time())
    with Session(engine) as session, session.begin():
        job = session.scalar(
            select(ValidationJob)
            .where(
                ValidationJob.done.is_(False),
                ValidationJob.available_at <= now,
                ValidationJob.lease_until <= now,
            )
            .order_by(ValidationJob.available_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        job.lease_id, job.lease_until = str(uuid.uuid4()), now + lease_seconds
        job.attempts += 1
        # Do not lock attempt here: callbacks lock attempts independently.
        attempt = session.get(Attempt, job.attempt_id)
        payload = {
            "attempt_id": attempt.id,
            "lease_id": job.lease_id,
            "deadline": job.deadline,
            "attempts": job.attempts,
            "state": attempt.state,
            "intent": attempt.intent,
            "observed": attempt.observed,
            "manifest": attempt.manifest,
            "config_body": attempt.config_body,
        }
        return payload


def finish_validation(engine, claim, result: str, evidence: dict, settings):
    now = int(time.time())
    with Session(engine) as session, session.begin():
        # Consistent lock order with callbacks: attempt, then job.
        attempt = session.scalar(
            select(Attempt).where(Attempt.id == claim["attempt_id"]).with_for_update()
        )
        job = session.scalar(
            select(ValidationJob).where(ValidationJob.attempt_id == attempt.id).with_for_update()
        )
        if job.lease_id != claim["lease_id"] or job.lease_until <= now:
            return False  # stale worker must not overwrite a newer lease
        if attempt.state in {"FAILED", "VALIDATED"}:
            job.done = True
        elif result == "valid":
            attempt.state, job.done = "VALIDATED", True
            add_event(session, attempt, "VALIDATED")
        elif (
            result == "fatal"
            or now >= job.deadline
            or job.attempts >= settings.validation_max_attempts
        ):
            attempt.state, attempt.failure_reason, job.done = "FAILED", evidence["reason"], True
            add_event(session, attempt, "FAILED")
        else:
            attempt.state = "VALIDATING"
            job.available_at = now + settings.validation_retry_seconds
        job.evidence = evidence
        job.lease_id, job.lease_until = None, 0
        return True
