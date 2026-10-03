"""Explicit per-device Lite reprovisioning after an operator erases a switch."""

import argparse
import time
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.persistence.database import build_engine
from app.persistence.models import Attempt, AttemptEvent, StatusGrant, ValidationJob
from app.settings import Settings


def prepare_reprovision(engine, serial, expected_attempt_id=None):
    """Archive the previous identity slot; retain its immutable plan and audit events."""
    with Session(engine) as session, session.begin():
        attempt = session.scalar(
            select(Attempt).where(Attempt.serial == serial.strip().upper()).with_for_update()
        )
        if attempt is None:
            raise ValueError("No current attempt found for that serial")
        if expected_attempt_id is not None and attempt.id != expected_attempt_id:
            raise ValueError("Attempt changed; refresh the dashboard before retrying")
        previous_id = attempt.id
        session.execute(delete(StatusGrant).where(StatusGrant.attempt_id == previous_id))
        job = session.get(ValidationJob, previous_id)
        if job:
            job.done = True
            job.lease_id = None
            job.lease_until = 0
        # Unique live identity slots are released; original identity remains in intent/observed.
        attempt.device_id = "archived:" + previous_id
        attempt.serial = "archived:" + previous_id
        attempt.state = "FAILED"
        attempt.failure_reason = "operator_reprovision"
        session.add(
            AttemptEvent(
                id=str(uuid.uuid4()),
                attempt_id=previous_id,
                kind="OPERATOR_REPROVISION",
                created_at=int(time.time()),
            )
        )
    return previous_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument(
        "--confirm-erased",
        action="store_true",
        help="Confirm the previous run is stopped and the switch was erased",
    )
    args = parser.parse_args()
    settings = Settings()
    if not settings.lite_mode or not args.confirm_erased:
        parser.error("Requires Lite mode and --confirm-erased; stop the previous run first")
    engine = build_engine(settings.database_url.get_secret_value())
    try:
        previous = prepare_reprovision(engine, args.serial)
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    finally:
        engine.dispose()
    print("Archived attempt " + previous + "; next registration creates a fresh attempt.")


if __name__ == "__main__":
    main()
