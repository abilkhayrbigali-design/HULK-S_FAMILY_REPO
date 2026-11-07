# Чингиз: первый серверный инкремент

Ветка `codex/chingiz`. Реализованы хранение снимков, начальный чек-лист,
редактор версий, JSON/HTTP-импорт, список и карточка диалогов.
В C-07 ветка синхронизирована с текущим `origin/main` (ea6ff60): каркас,
настройки и manifest/lock получены без изменений реализации Абулхаира.
Автоматическое подключение бизнес-маршрутов в `create_app` ещё не выполнено;
готовые модули подключаются через вход ниже. Worker/AI и апелляции отсутствуют.

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
интегратором. `install_data_api` использует `app.main.error_response` (по умолчанию
либо переданный явно), включая код в HTTP-сводке; диагностика переиспользует
`app.diagnostics.log_internal_error`. Локальные fallback-обработчики удалены
после C-07. Конфигурацию журналов устанавливает каркас, а не модули данных.

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

Установка и общая проверка выполняются из корня репозитория по lock:

```bash
make setup
make check
```

Синтетические HTTP-ответы проверяют адаптер, а не доступ к реальному DEV API.
Подписанная ссылка не предоставлена. Ключи и реальный AI в этих тестах не нужны.

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
маркеров в ответе/`caplog`. Итоговые проверки после C-07 (Python 3.13,
собственное окружение рабочей копии из неизменённого `backend/uv.lock`):

```bash
/tmp/dataton-review-tools/bin/uv sync --project backend --locked
make check UV=/tmp/dataton-review-tools/bin/uv
git diff --check
```

В ветке: **129 passed**, без пропусков; lint/форматирование/неизменность экспортов
прошли без отключения E501. До C-07 отдельно проходили 37 тестов данных,
1 интеграционный модуль пропускался без A-01; временная проверка с наложением
модулей на origin/main дала 129 passed. `test_data_core_integration.py` подключает API к настоящему
`create_app`: проверяет общий `error_response`, одну HTTP-сводку/одну диагностику
при сбое commit, уровни/коды и отсутствие приватных данных. Ruff check/format и
`git diff --check` прошли. В C-07 выполнен merge main в codex/chingiz с сохранением
истории обеих сторон; сама main не менялась. Автоматическое подключение API
в `main.py`, worker/AI и апелляции остаются за пределами этого ревью.
