# Чингиз: первый серверный инкремент

Ветка `codex/chingiz`. Реализованы хранение снимков, начальный чек-лист,
редактор версий, JSON/HTTP-импорт, список и карточка диалогов.
Это ещё не запускаемое целиком приложение: `main.py`, настройки и manifest/lock
остаются зоной Абулхаира. Отменённая реализация A-01/A-02 не восстанавливалась.

## Подключение к серверному каркасу

```python
from app.api.install import install_data_api
from app.db.migrate import migrate
from app.db.session import make_engine, session_factory
from app.main import error_response
from app.services.seed import seed

# Каталог БД должен существовать. URL и подписанная ссылка приходят из config.
database_url = settings.sqlalchemy_url.render_as_string(hide_password=False)
migrate(database_url)
engine = make_engine(database_url)
sessions = session_factory(engine)
with sessions.begin() as session:
    seed(session)
install_data_api(
    app, sessions,
    dataset_url=settings.dataset_api_url.get_secret_value(),
    error_response=error_response,
)
```

Пример согласован с текущим A-01 (`origin/main` ea6ff60): относительный путь
разрешается настройками от корня репозитория, подписанный URL извлекается из
SecretStr только на сервере. Не выводите URL в журнал. Миграцию/seed выполнить
перед приёмом запросов, engine.dispose() — при остановке. `/health` уже реализован
интегратором. `install_data_api` переиспользует переданный `app.main.error_response`,
включая код в HTTP-сводке; диагностика автоматически использует
`app.diagnostics.log_internal_error`, если каркас присутствует. Без A-01 остаются
безопасные локальные обработчики без текста исключений. Конфигурацию журналов
устанавливает каркас, а не модули данных.

Миграция запускается отдельно из корня репозитория:

```bash
PYTHONPATH=backend python -m app.db.migrate sqlite:////absolute/path/cqa.sqlite3
```

Все таблицы создаются Alembic, не `metadata.create_all()`: триггеры миграции
защищают снимки от UPDATE/DELETE. SQLite FK, WAL и busy timeout включаются на
каждом соединении. Head-указатели имеют составные FK, исключающие ссылку на
версию другого диалога/чек-листа. Миграция рассчитана на SQLite; разрушительный
`downgrade` намеренно не предоставлен.

При повторном импорте старого содержимого используется существующий снимок и
он становится текущим; `unchanged` означает отсутствие нового снимка. Язык,
канал и аудиоссылка сохраняются в версии, поэтому старая карточка сохраняет
исходные метаданные. Исходные timestamp и текст сообщений не обрезаются.

HTTP-запрос выполняется до транзакции импорта. Подпись сохраняет исходное
URL-кодирование, заменяется только `group_id`. Redirect не выполняется.
HTTPX/httpcore logging отключён до уровня CRITICAL: INFO/DEBUG этих библиотек
раскрывает подписанные URL. Не включать его обратно при интеграции. Сырые
исключения/ввод Pydantic не возвращаются клиенту.

## Проверка

Пока manifest отсутствует в этой ветке, временное окружение можно создать вне Git:

```bash
python -m venv /tmp/cqa-venv
/tmp/cqa-venv/bin/pip install fastapi==0.162.1 pydantic==2.14.3 sqlalchemy==2.1.8 alembic==1.20.1 httpx==0.28.1 httpx2==2.16.0 pytest==9.2.0 ruff==0.17.4
PYTHONPATH=backend /tmp/cqa-venv/bin/python -m pytest backend/tests/test_data_api.py -q -W error
/tmp/cqa-venv/bin/ruff check backend --select E,F,I,UP,B --ignore E501
/tmp/cqa-venv/bin/ruff format backend --check
```

Зависимости совместимы с диапазонами сохранённого manifest Абулхаира, но этот
manifest не перенесён. Итоговый lock и общую CI подтверждает интегратор.
В управляемой среде TestClient зависал при запуске event loop внутри sandbox;
тот же набор вне sandbox прошёл. Синтетические HTTP-ответы проверяют адаптер,
а не доступ к реальному DEV API. Подписанная ссылка не предоставлена.

## Оставшаяся работа

- C-01: подключить модули к A-01, согласовать схемы ответов/OpenAPI в A-02.
- C-02: таблицы/маршруты evaluations, worker, восстановление очереди и подключение
  реального `evaluation.pipeline`; `latest_evaluation` пока всегда null.
- C-03: подтвердить использование новой версии в реальном AI-запуске.
- C-04: апелляции, атомарные решения/revision и пересчёт через N-02.
- C-05: тесты состояния worker, исходного результата и гонок решений после
  реализации C-02/C-04. Текущие тесты покрывают данные, импорт и версии.

Общий API-контракт не менялся. Для полей метаданных чек-листа выбраны
`current_version_number`, `current_version_id`, `versions`, `current_version`;
A-02 должен закрепить эти имена в общих контрактных примерах и клиенте.

## Результат ревью C-06

Проверен коммит `b396f43` из GitHub PR №3. Сессия маршрута теперь использует
`Depends(..., scope="function")`: сбой commit возвращает безопасный 503 до ответа
и откатывает запись. Неизвестное имя поля исключается из ошибки валидации, так
как само может содержать приватный текст; индекс записи и родительский путь
сохраняются. Общая проверка URL отклоняет неверный порт, пробелы, управляющие
символы и credentials; dataset URL не допускает fragment. Корректные подписанные
URL сохраняются без нормализации. CLI миграций возвращает ненулевой код и
безопасную диагностику вместо traceback. Убраны повторная сборка ошибок,
избыточное перехватывание `ValidationError` (это `ValueError`) и лишний подсчёт
сообщений при чтении карточки.

Журналы и воспроизводимые сценарии:

- `cqa.import`: `dataset_fetch_started`, `dataset_fetch_rejected` (WARNING,
  `DATASET_UNAVAILABLE` для неверной конфигурации), `dataset_fetch_failed`
  (ERROR, `DATASET_ERROR` для upstream 500/повреждённого ответа),
  `dataset_import_completed` (INFO, только счётчики после commit).
- `cqa.checklists`: `checklist_version_saved` (INFO после commit),
  `checklist_version_conflict` (WARNING, `VERSION_CONFLICT` при повторном сохранении).
- `cqa.data`: `data_write_conflict` (WARNING, `STATE_CONFLICT`).
- `cqa.errors`: `DATABASE_UNAVAILABLE` (ERROR при синтетическом сбое commit),
  `MIGRATION_FAILED`/`MIGRATION_INVALID_ARGUMENTS` (ERROR для CLI).
  `cqa.db`: `database_migration_completed` (INFO).

Тест commit проверяет загрузку JSON, dataset-импорт, создание чек-листа и новой
версии: rollback, сохранность истории, отсутствие успешного события и приватных
маркеров в ответе/`caplog`. Фактически выполнены (Python 3.13, зависимости
окружения интегратора; `-S` исключает его editable-привязку к другой рабочей копии):

```bash
CQA_REVIEW_PYTHON=/home/abilkhayr/Work/Dataton/backend/.venv/bin/python
CQA_REVIEW_SITE=/home/abilkhayr/Work/Dataton/backend/.venv/lib/python3.13/site-packages
PYTHONPATH="backend:$CQA_REVIEW_SITE" "$CQA_REVIEW_PYTHON" -S -m pytest backend/tests -q -W error
/home/abilkhayr/Work/Dataton/backend/.venv/bin/ruff check backend --select E,F,I,UP,B --ignore E501
/home/abilkhayr/Work/Dataton/backend/.venv/bin/ruff format backend --check
git diff --check
```

В ветке: **37 passed, 1 skipped** (интеграционный модуль пропущен без A-01).
Во временной копии `git archive origin/main` (`ea6ff60`) с наложенными каталогами
`backend/app`, `backend/tests`, `backend/alembic` этой ветки та же команда pytest
дала **129 passed**. `test_data_core_integration.py` подключает API к настоящему
`create_app`: проверяет общий `error_response`, одну HTTP-сводку/одну диагностику
при сбое commit, уровни/коды и отсутствие приватных данных. Ruff check/format и
`git diff --check` прошли. Ветки не объединялись; автоматическое подключение API
в `main.py`, worker/AI и апелляции остаются за пределами этого ревью.
