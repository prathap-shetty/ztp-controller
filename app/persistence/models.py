from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.database import Base


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('AUTHORIZED','CONFIGURING','VALIDATING','VALIDATED','FAILED')",
            name="m2a_states",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # M1 has no terminal transition or retry endpoint: one durable plan per device.
    device_id: Mapped[str] = mapped_column(String(128), unique=True)
    serial: Mapped[str] = mapped_column(String(128), unique=True)
    plan_hash: Mapped[str] = mapped_column(String(64))
    intent: Mapped[dict] = mapped_column(JSON)
    observed: Mapped[dict] = mapped_column(JSON)
    manifest: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(32), default="AUTHORIZED")
    created_at: Mapped[int] = mapped_column(BigInteger)
    config_body: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(String(64))


class StatusGrant(Base):
    __tablename__ = "status_grants"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"), index=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)


class RegistrationBucket(Base):
    __tablename__ = "registration_buckets"
    source_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    minute: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class AttemptEvent(Base):
    __tablename__ = "attempt_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[int] = mapped_column(BigInteger)


class ValidationJob(Base):
    __tablename__ = "validation_jobs"
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"), primary_key=True)
    available_at: Mapped[int] = mapped_column(BigInteger, index=True)
    deadline: Mapped[int] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[int] = mapped_column(BigInteger, default=0)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[dict | None] = mapped_column(JSON)
