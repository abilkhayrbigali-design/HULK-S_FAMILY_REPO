# Чингиз: первый серверный инкремент

Ветка `codex/chingiz`. Реализованы хранение снимков, начальный чек-лист,
редактор версий, JSON/HTTP-импорт, список и карточка диалогов.
В C-07 ветка синхронизирована с текущим `origin/main` (ea6ff60): каркас,
настройки и manifest/lock получены без изменений реализации Абулхаира.
Готовы маршруты оценок и апелляций, а также SQLite-очередь с внедряемым
провайдером. Точки подключения к `create_app` и N-01 приведены ниже.

## Подключение к серверному каркасу

```python
from app.api.install import install_data_api
from app.db.migrate import migrate
from app.db.session import session_factory
from app.diagnostics import log_internal_error
from app.services.evaluation_worker import evaluation_worker
from app.services.seed import seed

# После создания приложения и до начала lifespan.
install_data_api(app, dataset_url=settings.dataset_api_url.get_secret_value())

# Внутри существующего lifespan после создания application.state.engine.
try:
    migrate(engine=application.state.engine)
    sessions = session_factory(application.state.engine)
    with sessions.begin() as db:
        seed(db)
except Exception as exc:
    log_internal_error("DATA_STARTUP_FAILED", exc)
    raise RuntimeError("DATA_STARTUP_FAILED") from None
async with evaluation_worker(
    application,
    sessions,
    provider,
    concurrency=settings.evaluation_concurrency,
    prompt_version=prompt_version,
):
    yield
```

Абулхаир передаёт `provider` после проверки `settings.require_llm()` и создания
реального адаптера N-01. При отсутствующем ключе или адаптере передаётся `None`.
Тогда новый POST возвращает 503, а оставшиеся queued завершаются с
`AI_NOT_CONFIGURED`. Вызов `install_data_api` выполняется один раз.
Миграция `c002` использует тот же engine, worker завершается до `engine.dispose()`.
Очередь требует файловую SQLite. Режим `:memory:` с одним общим соединением
подходит для health-check каркаса, но worker в этом режиме не запускается.
При штатной остановке worker дожидается уже захваченных задач. Провайдер
обеспечивает конечный таймаут и ограниченные повторы согласно N-01/N-05.
Подписанная ссылка извлекается из SecretStr только на сервере. `/health` реализован
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
- C-02: подтвердить подключение реального провайдера N-01 в A-03. Маршруты,
  `latest_evaluation`, worker и восстановление проверяются с тестовым провайдером.
- C-03: подтвердить использование новой версии в реальном AI-запуске.
- C-05: подтвердить сценарии на интегрированной сборке с N-01. Тесты SQLite/API
  покрывают данные, импорт, версии, восстановление и гонку решений.

Общий API-контракт не менялся. Для полей метаданных чек-листа выбраны
`current_version_number`, `current_version_id`, `versions`, `current_version`;
A-02 должен закрепить эти имена в общих контрактных примерах и клиенте.

## Контракт провайдера и диагностика

`await provider.evaluate(transcript_snapshot, checklist_snapshot, prompt_version)`
возвращает словарь или модель ProviderResult. Провайдер получает отдельные копии
снимков. Опциональный `estimated_cost_usd` дополняет внутренний результат, если
тариф известен. При ошибке адаптер может поднять `ProviderFailure(metadata)`
из `app.services.evaluation_worker` с доступными полями provenance. Иначе
usage и стоимость остаются неизвестными. Worker сохраняет известный provenance
до N-02. Аварийное завершение во время самого сетевого вызова сохраняет только
уже записанные данные. Контракт запросов и ответов HTTP сохраняется.
Тесты сверяют реальные тела ответов с экспортированной схемой. Сверка
генерируемой FastAPI документации ответов остаётся в A-06.

`cqa.worker` пишет `evaluation_queued`, `evaluation_started`,
`evaluation_completed`, `evaluation_failed` и агрегат `evaluations_interrupted`.
При недоступной БД worker удерживает запись в слоте и повторяет сохранение
с паузами до двух секунд. Событие `evaluation_write_retry` отмечает начало
сбоя. Вызов модели при этом не повторяется. При остановке и недоступной БД
запуск остаётся running для восстановления в следующем процессе.
`cqa.appeals` пишет `appeal_created` и `appeal_decision_saved` после commit,
а при повторном решении или устаревшей revision пишет `appeal_conflict`.
Невалидное доказательство воспроизводит `AI_INVALID_RESULT`, ошибка
тестового провайдера — `AI_PROVIDER_ERROR`. Тесты проверяют уровни, коды,
сохранённый статус и отсутствие приватного маркера в журнале.

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
