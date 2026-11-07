"""Run: PYTHONPATH=backend python -m app.db.migrate sqlite:////absolute/path/cqa.sqlite3"""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config


def migrate(url):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    migrate(sys.argv[1])
