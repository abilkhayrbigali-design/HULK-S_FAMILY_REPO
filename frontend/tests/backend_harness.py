"""Run the production app for browser tests with a fresh SQLite file and no keys.

Uses a fresh temporary SQLite database and no external credentials. Logs contain
only the backend's existing safe events; access logs remain disabled.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from app.config import Settings
from app.main import create_app

with TemporaryDirectory(prefix="cqa-browser-") as directory:
    url = f"sqlite:///{Path(directory) / 'synthetic.sqlite3'}"
    settings = Settings(
        _env_file=None, CQA_DATABASE_URL=url, CQA_DATASET_API_URL="", OPENAI_API_KEY=""
    )
    uvicorn.run(
        create_app(settings), host="127.0.0.1", port=8000, access_log=False, log_level="warning"
    )
