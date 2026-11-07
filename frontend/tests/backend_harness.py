"""Browser-test-only composition of existing backend modules, no production changes.

Uses a fresh temporary SQLite database and no external credentials. Logs contain
only the backend's existing safe events; access logs remain disabled.
"""
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from app.api.install import install_data_api
from app.config import Settings
from app.db.migrate import migrate
from app.db.session import session_factory
from app.main import create_app, error_response
from app.services.seed import seed

with TemporaryDirectory(prefix="cqa-browser-") as directory:
    url = f"sqlite:///{Path(directory) / 'synthetic.sqlite3'}"
    settings = Settings(_env_file=None, CQA_DATABASE_URL=url, CQA_DATASET_API_URL="", OPENAI_API_KEY="")
    app = create_app(settings)
    migrate(url)
    original_lifespan = app.router.lifespan_context
    install_data_api(app, None, error_response=error_response)

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            sessions = session_factory(application.state.engine)
            application.state.session_factory = sessions
            with sessions.begin() as db:
                seed(db)
            yield

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False, log_level="warning")
