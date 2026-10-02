import hashlib
import secrets
import time
import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.errors import ZtpError
from app.models.contracts import DeviceIntent, Manifest, ObservedDevice, RegistrationResponse
from app.persistence.models import (
    Attempt,
    AttemptEvent,
    RegistrationBucket,
    RegistrationFailure,
    StatusGrant,
    ValidationJob,
)
from app.services.hashing import stable_hash
from app.vendors.nxos_version import version_satisfies


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Repository:
    def __init__(self, engine):
        self.engine = engine

    def rate_limit(self, source: str, limit: int):
        minute = int(time.time()) // 60
        with Session(self.engine) as session, session.begin():
            statement = (
                insert(RegistrationBucket)
                .values(
                    source_hash=digest_token(source),
                    minute=minute,
                    count=1,
                )
                .on_conflict_do_update(
                    index_elements=[RegistrationBucket.source_hash, RegistrationBucket.minute],
                    set_={"count": RegistrationBucket.count + 1},
                )
                .returning(RegistrationBucket.count)
            )
            count = session.execute(statement).scalar_one()
        if count > limit:
            raise ZtpError("rate_limited", 429, "Registration rate exceeded; retry later")

    def record_registration_failure(self, serial, stage, error):
        import logging

        from sqlalchemy.exc import SQLAlchemyError

        try:
            with Session(self.engine) as session, session.begin():
                session.add(
                    RegistrationFailure(
                        id=str(uuid.uuid4()),
                        serial=serial,
                        stage=stage,
                        code=error.code,
                        message=error.message[:256],
                        created_at=int(time.time()),
                    )
                )
                session.flush()
                keep = (
                    select(RegistrationFailure.id)
                    .order_by(RegistrationFailure.created_at.desc(), RegistrationFailure.id.desc())
                    .limit(1000)
                )
                session.execute(
                    delete(RegistrationFailure).where(RegistrationFailure.id.not_in(keep))
                )
        except SQLAlchemyError:
            logging.getLogger(__name__).warning("Registration diagnostic could not be persisted")

    def register(
        self,
        intent: DeviceIntent,
        observed: ObservedDevice,
        manifest: Manifest,
        ttl: int,
        config_body: str | None = None,
        validation_delay: int = 90,
        validation_deadline: int = 1800,
    ) -> RegistrationResponse:
        plan = manifest.model_dump(mode="json")
        plan_hash = stable_hash(plan)
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        with Session(self.engine) as session, session.begin():
            inserted = session.execute(
                insert(Attempt)
                .values(
                    id=str(uuid.uuid4()),
                    device_id=intent.device.id,
                    serial=intent.device.serial_number,
                    plan_hash=plan_hash,
                    intent=intent.model_dump(mode="json"),
                    observed=observed.model_dump(mode="json"),
                    manifest=plan,
                    state="AUTHORIZED",
                    created_at=now,
                    config_body=config_body,
                )
                .on_conflict_do_nothing()
                .returning(Attempt.id)
            ).scalar_one_or_none()
            if inserted:
                session.add(
                    AttemptEvent(
                        id=str(uuid.uuid4()), attempt_id=inserted, kind="AUTHORIZED", created_at=now
                    )
                )
            if inserted and config_body is not None and manifest.validation_policy == "ssh":
                session.add(
                    ValidationJob(
                        attempt_id=inserted,
                        available_at=now + validation_delay,
                        deadline=now + validation_deadline,
                        attempts=0,
                        lease_until=0,
                        done=False,
                    )
                )
            attempt = session.scalar(select(Attempt).where(Attempt.device_id == intent.device.id))
            resumed = False
            if attempt is not None and attempt.manifest["mode"] == "upgrade-and-configure":
                old = dict(attempt.observed)
                old["current_version"] = observed.current_version
                candidate = dict(plan)
                candidate["upgrade_required"] = attempt.manifest["upgrade_required"]
                candidate["actions"] = attempt.manifest["actions"]
                resumed = (
                    version_satisfies(
                        observed.current_version,
                        attempt.manifest["target"]["target_version"],
                        attempt.manifest.get("allow_newer_version", False),
                    )
                    and old == observed.model_dump(mode="json")
                    and candidate == attempt.manifest
                )
            if (
                attempt is None
                or attempt.state in {"FAILED", "VALIDATED"}
                or (
                    not resumed
                    and (
                        attempt.plan_hash != plan_hash
                        or attempt.observed != observed.model_dump(mode="json")
                    )
                )
            ):
                raise ZtpError("attempt_conflict", 409, "Existing attempt requires reconciliation")
            session.add(
                StatusGrant(
                    token_hash=digest_token(token),
                    attempt_id=attempt.id,
                    expires_at=now + ttl,
                )
            )
            return RegistrationResponse(
                provisioning_id=attempt.id,
                state=attempt.state,
                plan_hash=attempt.plan_hash,
                manifest=Manifest.model_validate(attempt.manifest),
                status_token=token,
                status_token_expires_at=now + ttl,
            )

    def status(self, attempt_id: str, token: str) -> Attempt:
        with Session(self.engine) as session:
            grant = session.get(StatusGrant, digest_token(token))
            if not grant or grant.attempt_id != attempt_id or grant.expires_at <= int(time.time()):
                raise ZtpError("invalid_status_token", 401, "Valid attempt status token required")
            attempt = session.get(Attempt, attempt_id)
            if attempt is None:
                raise ZtpError("not_found", 404, "Attempt not found")
            session.expunge(attempt)
            return attempt

    def validation_evidence(self, attempt_id: str) -> dict | None:
        with Session(self.engine) as session:
            job = session.get(ValidationJob, attempt_id)
            return job.evidence if job else None

    def cleanup(self) -> None:
        now = int(time.time())
        with Session(self.engine) as session, session.begin():
            session.execute(delete(StatusGrant).where(StatusGrant.expires_at <= now))
            session.execute(
                delete(RegistrationBucket).where(
                    RegistrationBucket.minute < now // 60 - 2,
                )
            )
