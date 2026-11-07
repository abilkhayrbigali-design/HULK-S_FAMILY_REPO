from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def make_engine(url: str):
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite:") else {},
    )
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def sqlite_settings(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
