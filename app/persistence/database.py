from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def build_engine(url: str) -> Engine:
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("The controller requires a PostgreSQL psycopg database URL")
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)
