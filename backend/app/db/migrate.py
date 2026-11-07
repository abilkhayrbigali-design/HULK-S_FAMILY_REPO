"""Run: PYTHONPATH=backend python -m app.db.migrate sqlite:////absolute/path/cqa.sqlite3"""

import logging
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.diagnostics import log_internal_error


def migrate(url):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        logging.getLogger("cqa.errors").error("error=MIGRATION_INVALID_ARGUMENTS")
        return 1
    try:
        migrate(arguments[0])
    except Exception as exc:
        log_internal_error("MIGRATION_FAILED", exc)
        return 1
    logging.getLogger("cqa.db").info("event=database_migration_completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
