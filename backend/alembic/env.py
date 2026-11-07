from alembic import context
from app.db.session import make_engine
from app.models import evaluations  # noqa: F401  # Register evaluation tables on Base metadata.
from app.models.data import Base

config = context.config


def run_on_connection(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    shared = config.attributes.get("connection")
    if shared is not None:
        run_on_connection(shared)
    else:
        engine = make_engine(config.get_main_option("sqlalchemy.url"))
        try:
            with engine.connect() as connection:
                run_on_connection(connection)
        finally:
            engine.dispose()
