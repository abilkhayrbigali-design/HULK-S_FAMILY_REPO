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
from app.services.seed import seed

# Каталог БД должен существовать. URL и подписанная ссылка приходят из config.
migrate(settings.database_url)
engine = make_engine(settings.database_url)
sessions = session_factory(engine)
with sessions.begin() as session:
    seed(session)
install_data_api(app, sessions, dataset_url=settings.dataset_api_url)
```

Имена полей `settings` здесь иллюстративные: интегратор сопоставляет их со своим
config. Миграцию/seed выполнить перед приёмом запросов, engine.dispose() — при
остановке. `/health` реализует интегратор, проверяя реальное подключение к БД.
`install_data_api` устанавливает обработчики ошибок в согласованном формате;
при интеграции объединить их с общими обработчиками A-01.

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

Пока manifest отсутствует в main, временное окружение можно создать вне Git:

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
