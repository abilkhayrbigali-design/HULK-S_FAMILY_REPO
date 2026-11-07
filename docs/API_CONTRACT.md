# API-контракт v0.1

Статус: целевой контракт, 2026-11-07. Реализован серверный каркас с `/health`; бизнес-маршруты и клиент ещё отсутствуют. Базовый путь `/api/v1`, JSON UTF-8. A-02 добавляет проверяемые схемы/фикстуры; согласование Pydantic и runtime OpenAPI подтверждается после реализации C/N/R.

Машинный источник структуры — `contracts/definitions.py`, экспорт — `contracts/openapi.json`; `make contracts` обновляет экспорт, `make check` проверяет схемы и собственные примеры. Текстовые бизнес-ограничения ниже обязательны и там, где JSON Schema не может проверить уникальность ID, принадлежность версии или пересчёт. Уточнения A-02 зафиксированы в D-007 и `docs/INTEGRATION.md`.

## Общие соглашения

- Внутренние ID — UUID-строки. Внешний `conversation_id` организаторов хранится отдельно как `external_id`.
- Время создания ресурсов — ISO 8601 UTC. Исходный timestamp сообщения сохраняется строкой без изменения.
- `language`: `RU | KZ | EN`; `channel`: `agent chat | voice call`; `speaker`: `client | operator | bot | system`.
- Ответы обычных маршрутов не оборачиваются в `data`. Списки: `{ "items": [], "total": 0, "limit": 50, "offset": 0 }`; `limit` от 1 до 100.
- Ошибка HTTP: `{ "error": { "code": "VALIDATION_ERROR", "message": "Понятное описание", "details": [] } }`. `details` не содержит секретов и сырых upstream-исключений.
- Элемент `details`: `{ "loc": ["body", "records", 0, "transcript"], "type": "too_short" }`; `loc` состоит из строк/целых индексов, `type` — безопасный код, без исходного `input`, `ctx` и текста исключения.
- Общий обработчик HTTPException сохраняет служебные заголовки `Allow` и `Retry-After` без учёта регистра имени; произвольные заголовки и исходный detail исключения не передаются. `POST /health` возвращает 405 с `Allow: GET` и стандартным безопасным JSON-телом.
- Основные статусы: 200 чтение/импорт, 201 создание, 202 постановка оценки, 404 отсутствующий ресурс, 409 конфликт версии/состояния, 422 невалидный ввод, 502 сбой внешнего API, 503 недоступный AI/конфигурация.
- Pydantic должен строго различать `false`, `0` и `null`: `0` не превращается в бинарное «Нет», `true` не превращается в числовую ступень 1.
- Баллы и веса в JSON — конечные числа; итог может быть `null`. Интерфейс форматирует баллы до двух знаков. Внутри расчёта используются Decimal.
- Поля объектов, перечисленные в машинной схеме, передаются явно; nullable-поле не пропускается. Неизвестные поля отклоняются стартовым контрактом; согласованное расширение требует обновления схем и потребителей.

## 1. Импорт и диалоги

| Метод и путь | Вход | Выход/поведение |
| --- | --- | --- |
| `GET /health` | Без параметров; вне `/api/v1` | 200 `{ "status": "ok" }` при доступной БД; ключ AI и URL не раскрываются |
| `POST /datasets/import` | `{ "group_id": "DEV" }` | Импорт по серверному URL; сводка ниже |
| `POST /conversations/import` | `{ "records": [OrganizerRecord] }` | Импорт загруженного JSON; source=`upload`, отдельная стабильная группа `UPLOAD` |
| `GET /conversations` | `language?`, `channel?`, `group_id?`, `limit`, `offset` | Список `ConversationSummary` |
| `GET /conversations/{id}` | `transcript_version_id?` | Карточка и сообщения указанной версии; без параметра последняя версия |

Внешний ответ организаторов имеет оболочку `error`, `isSuccess`, `data.dataset_group_id`, `data.total_records`, `data.records`. Эта оболочка разбирается только адаптером импорта. В UI загрузка такого файла извлекает `data.records` перед отправкой в `/conversations/import`.

`OrganizerRecord`:

```json
{
  "conversation_id": "synthetic-ru-001",
  "channel": "agent chat",
  "language": "RU",
  "transcript": [
    { "timestamp": "00:01", "speaker": "client", "text": "Как найти справку?" },
    { "timestamp": "00:03", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алия." }
  ],
  "audio_url": null
}
```

Это собственный синтетический пример, а не запись из DEV. Для чата `audio_url=null`, для звонка — URL, если он предоставлен. Пустой массив сообщений и неизвестные роли отклоняются с 422. Валидируется весь импорт до записи; при ошибке одного элемента импорт целиком отклоняется с указанием индекса/поля.

Сводка импорта:

```json
{
  "group_id": "DEV",
  "received": 65,
  "created": 65,
  "new_versions": 0,
  "unchanged": 0,
  "conversation_ids": ["00000000-0000-4000-8000-000000000001"]
}
```

В примере список ID сокращён. В реальном ответе он содержит внутренние ID всех импортированных диалогов; `created + new_versions + unchanged = received`. Повтор одного внешнего ID внутри одного payload отклоняется, а не считается дважды.

`ConversationSummary`: `id`, `external_id`, `source`, `group_id`, `language`, `channel`, `current_transcript_version_id`, `message_count`, `latest_evaluation` (краткий объект или null).

`source`: `dataset` для импорта организаторов, `upload` для JSON. `latest_evaluation`: `{ id, status, original_total, final_total, created_at }`; итоги nullable до завершения или при отсутствии применимых весов. Выбирается последний созданный запуск.

Карточка дополнительно содержит `transcript_version_id`, `audio_url`, `messages`. Сообщение:

```json
{ "id": "m0001", "position": 1, "timestamp": "00:01", "speaker": "client", "text": "Как найти справку?" }
```

ID сообщения уникален в пределах версии транскрипта. Сервер проверяет принадлежность запрошенной версии данному диалогу. Аудиоссылки возвращаются только для карточки; не включаются в логи, публичные фикстуры или отчёты с агрегированными метриками.

## 2. Чек-листы и версии

| Метод и путь | Назначение |
| --- | --- |
| `GET /checklists` | Пагинируемый список `{ id, name, latest_version_id, latest_version_number }` |
| `POST /checklists` | `{ name, criteria }` создаёт чек-лист и версию 1; возвращает `ChecklistVersion` |
| `GET /checklists/{id}` | `{ id, name, current_version, versions }`; current_version и элементы versions — полные ChecklistVersion |
| `GET /checklist-versions/{id}` | Полный неизменяемый снимок `ChecklistVersion` |
| `POST /checklists/{id}/versions` | `{ expected_version_number, criteria }` создаёт новую версию; при устаревшем номере 409 |

`ChecklistVersion`: `id`, `checklist_id`, `name`, `version_number`, `created_at`, `criteria`.

Определение критерия:

```json
{
  "id": "management",
  "name": "Управление диалогом, скорость и грамотность",
  "instruction": "Оцените ясность, последовательность и предупреждение об ожидании по описанной шкале.",
  "type": "scale",
  "weight": 15,
  "allow_na": false,
  "na_rule": null,
  "scale": [
    { "value": 0, "description": "Ход диалога непонятен из-за бессвязных или противоречивых реплик" },
    { "value": 4, "description": "Клиент переспрашивает из-за формулировок или порядка ответов" },
    { "value": 7, "description": "Один заметный сбой; клиент может понять ответ и продолжить" },
    { "value": 10, "description": "Ясно и связно; при явном ожидании оператор предупредил" }
  ],
  "section": "Ведение и завершение диалога"
}
```

- `type`: `binary | scale | flag`.
- Для `binary`: положительный вес, `scale=null`, ответ boolean.
- Для `scale`: положительный вес, минимум две уникальные конечные неотрицательные ступени с описаниями, максимальная ступень > 0; ответ строго из шкалы.
- Для `flag`: вес 0, `scale=null`, `allow_na=false`, `na_rule=null`, ответ boolean. `true` обозначает обнаруженное нарушение/событие.
- `allow_na=true` требует непустого `na_rule`; иначе `na_rule=null`.
- Непустые `id`, `name`, `instruction`; ID уникальны в версии. ID сохраняется при редактировании того же критерия; новый критерий получает новый ID.
- `section` — строка или null. Хотя бы один балльный критерий; вес отрицательный/NaN/Infinity недопустим.
- В новой версии можно добавить/удалить/заменить критерий, изменить формулировку, тип, вес, N/A и шкалу. Старые версии не меняются.

Начальный чек-лист из восьми критериев создаётся seed-операцией, применяемой повторно без дубликатов. Все ступени и формулировки берутся из `SCORING.md`, а не из сокращённого примера выше.

## 3. Запуск и результат оценки

| Метод и путь | Вход/выход |
| --- | --- |
| `POST /evaluations` | `{ conversation_id, checklist_version_id }` → 202 `Evaluation`; фиксируется текущая версия транскрипта |
| `GET /evaluations` | Фильтр `conversation_id?`, `status?`, пагинация; список запусков |
| `GET /evaluations/{id}` | Полный `Evaluation`, включая исходные и финальные результаты |

`Evaluation`:

| Поле | Смысл |
| --- | --- |
| `id`, `conversation_id` | Внутренние ID |
| `transcript_version_id`, `checklist_version_id` | Зафиксированные версии |
| `status` | `queued`, `running`, `completed`, `failed` |
| `revision` | 0 до решений; увеличивается при каждом сохранённом решении по апелляции |
| `created_at`, `completed_at` | UTC; второе может быть null |
| `original`, `final` | `Assessment` или null до завершения/при ошибке |
| `review_required`, `review_reasons` | Признак необходимости ручной проверки и конкретные причины |
| `provenance` | null до вызова; иначе provider, model_requested, nullable model_reported, prompt_version, duration_ms, usage всех попыток, nullable estimated_cost_usd |
| `error` | null или `{ code, message, details }` с безопасным содержимым |

У `queued/running` completed_at/error/original/final равны null. У `failed` error и completed_at заполнены, original/final равны null. У `completed` completed_at, provenance, original/final заполнены, error=null. Revision до решений равна 0. При ошибке после оплаченного вызова известное provenance/usage сохраняется; отсутствие подтверждённого usage не заменяется нулём.

`usage`: `{ request_count, input_tokens, output_tokens, total_tokens, cached_input_tokens, reasoning_output_tokens }`. request_count — неотрицательное целое, включая все попытки; каждый токенный счётчик — неотрицательное целое либо null при неизвестном/неполном значении. Cached/reasoning являются частью input/output, не прибавляются к ним повторно. Неизвестная стоимость null. Для live-завершения нужен хотя бы один фактический запрос; синтетические примеры не доказывают его выполнение.

`Assessment`: `total` (0..100 или null), `no_applicable_reason` (строка или null), `gross_error` (boolean или null), `criteria`, `sections`.

`gross_error=null`, если в выбранном пользовательском чек-листе нет флага с ID `gross_errors`; иначе значение этого флага. Остальные пользовательские флаги остаются в `criteria` и не объединяются автоматически в грубые нарушения.

`sections`: массив `{ name, base_points, applicable_weight, total }`. `total` секции нормализуется по применимым весам внутри секции; при нулевом знаменателе null. Итог диалога вычисляется по всем критериям, а не как среднее секций. Флаги не влияют на итоги секций.

Элемент `criteria`:

```json
{
  "criterion_id": "management",
  "applicability": "applicable",
  "answer": 7,
  "weight": 15,
  "base_points": 10.5,
  "effective_weight": 15,
  "effective_points": 10.5,
  "explanation": "Один заметный сбой в последовательности ответа.",
  "evidence": [{ "kind": "message", "message_id": "m0002" }],
  "review_required": false,
  "review_reasons": []
}
```

Пример строки предполагает начальный чек-лист без N/A у других критериев. Допустимость ответа определяется выбранной версией. У `not_applicable` ответ null, вклад 0, effective_weight 0; для `flag` все числовые вклады 0. `weight` всегда исходный вес, а не перераспределённый.

Форматы доказательств:

```json
{ "kind": "message", "message_id": "m0002" }
```

```json
{ "kind": "range", "start_message_id": "m0001", "end_message_id": "m0008" }
```

Диапазон включительный, непустой, с правильным порядком сообщений. Модель не присылает «цитату», которую интерфейс считает истинной без проверки. Отображаемый текст берётся из связанной версии транскрипта.

Каждый критерий возвращается ровно один раз и имеет непустые объяснение и доказательства. Нельзя добавить неизвестный критерий или опустить существующий. Для N/A объяснение содержит условие неприменимости; для пропуска действия — ожидаемое действие и проверенный диапазон. Содержательная корректность дополнительно оценивается по `TEST_PLAN.md`.

Внутренний ответ провайдера содержит `criterion_id`, `applicability`, `answer`, `explanation`, `evidence`, `review_reasons`; все веса/баллы добавляются только сервером. Provider-level метаданные определены в `ARCHITECTURE.md`.

ProviderResult: `{ criterion_results, provider, model_requested, model_reported, usage, duration_ms, review_reasons }`; model_reported nullable, review_reasons — массив непустых строк. Структура snapshot и точная async-граница описаны в INTEGRATION.md. Общая JSON Schema проверяет внутренний результат; это не готовая схема для прямого вызова Structured Outputs. `review_required` выводится из причин результатов/провайдера при формировании Evaluation.

## 4. Апелляции и решения

| Метод и путь | Вход/выход |
| --- | --- |
| `POST /evaluations/{id}/appeals` | `{ criterion_id, comment }` → 201 `Appeal` |
| `GET /appeals` | `status?`, `evaluation_id?`, пагинация; список |
| `GET /appeals/{id}` | `Appeal` со связанными исходным/текущим ответами, доказательствами, комментарием и revision |
| `POST /appeals/{id}/decisions` | Решение ниже → 200 `{ appeal, evaluation }` с пересчитанным финальным результатом |

`Appeal`: `id`, `evaluation_id`, `criterion_id`, `comment`, `status=open|resolved`, `base_revision`, `current_revision`, `created_at`, `decision` или null. `comment` и причина решения непустые. Обращение допустимо только для завершённой оценки и существующего критерия.

Во всех ответах Appeal также содержит `original` и `current` — полные CriterionAssessment, со всеми доказательствами и исходным весом. `decision`: `{ id, appeal_id, action, reason, expected_revision, created_at, result }`. result — полный принятый ProviderCriterionResult без вычисляемых весов; confirm фиксирует текущий финальный ответ, override — новый. Открытый Appeal имеет decision=null, resolved — сохранённое решение. Историю обращений можно получить фильтром `GET /appeals?evaluation_id=...`.

Подтверждение:

```json
{ "action": "confirm", "reason": "Исходный вывод подтверждён указанным фрагментом.", "expected_revision": 0 }
```

Изменение:

```json
{
  "action": "override",
  "reason": "Оператор дал корректный следующий шаг; существенные вопросы закрыты.",
  "expected_revision": 0,
  "applicability": "applicable",
  "answer": 20,
  "evidence": [{ "kind": "message", "message_id": "m0008" }]
}
```

`confirm` сохраняет текущий финальный ответ и его доказательства; при первом решении он совпадает с исходным AI-ответом. `override` требует полного нового ответа/применимости и валидных доказательств; проверяется по исходной версии чек-листа и транскрипта.

На evaluation+criterion разрешено одно открытое обращение. После закрытия можно подать новое, сохранив предыдущую историю. Решение атомарно закрывает обращение, сохраняет неизменяемую запись, увеличивает revision и пересчитывает `final`. `original` не меняется. Повторное решение закрытого обращения или устаревшая revision — 409.

## 5. Совместимость команд

- Чингиз владеет реализацией схем и маршрутов; Артём — типизированным клиентом и состояниями интерфейса.
- Нурсултан согласует внутренний `ProviderResult` и валидацию критериев/доказательств с Чингизом.
- Абулхаир фиксирует синтетические контрактные примеры в A-02 и проверяет сквозную совместимость.
- Новое поле по возможности добавляется совместимо. Переименование/удаление поля требует общей правки документов, клиента, сервера и проверок в согласованном порядке.
- Декларативный контракт не является готовым сервером. Согласованные маршруты должны быть подтверждены первым работающим сквозным сценарием A-03.
