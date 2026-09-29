"""Maintain grants and run bounded read-only M2a validation jobs."""

import logging
import signal
from threading import Event

from sqlalchemy.exc import SQLAlchemyError

from app.inventory.factory import build_provider
from app.persistence.database import build_engine
from app.persistence.repository import Repository
from app.services.validation import SshValidator, validate_once
from app.settings import Settings


def main():
    logging.basicConfig(level=logging.INFO)
    stopped = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    settings = Settings()
    engine = build_engine(settings.database_url.get_secret_value())
    inventory = build_provider(settings)
    validator = SshValidator(settings)
    try:
        while not stopped.is_set():
            try:
                Repository(engine).cleanup()
                validate_once(engine, inventory, validator, settings)
            except SQLAlchemyError:
                logging.error("Maintenance storage unavailable; retrying in 30 seconds")
            stopped.wait(5)
    finally:
        inventory.close()
        engine.dispose()


if __name__ == "__main__":
    main()
