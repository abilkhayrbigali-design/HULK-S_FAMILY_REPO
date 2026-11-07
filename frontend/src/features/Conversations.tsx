import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api/context";
import { useResource } from "../api/useResource";
import { ApiError, logEvent } from "../api/errors";
import {
  importSchema,
  type Channel,
  type ImportSummary,
  type Language,
} from "../api/schema";
import {
  ChannelTag,
  OpenArrow,
  Empty,
  ErrorNotice,
  Loading,
  PageTitle,
} from "../components/common";

export function Conversations() {
  const api = useApi();
  const [language, setLanguage] = useState<Language | "">("");
  const [channel, setChannel] = useState<Channel | "">("");
  const [offset, setOffset] = useState(0);
  const [group, setGroup] = useState("DEV");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState<unknown>();
  const [result, setResult] = useState<ImportSummary>();
  const fileRef = useRef<HTMLInputElement>(null);
  const list = useResource(
    useCallback(
      (signal) =>
        api.conversations(
          {
            language: language || undefined,
            channel: channel || undefined,
            offset,
            limit: 20,
          },
          signal,
        ),
      [api, language, channel, offset],
    ),
  );
  async function performImport(file?: File) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(undefined);
    setResult(undefined);
    try {
      let summary: ImportSummary;
      if (file) {
        if (file.size > 10 * 1024 * 1024) throw new ApiError("FILE_TOO_LARGE");
        let data: unknown;
        try {
          data = JSON.parse(await file.text());
        } catch {
          throw new ApiError("INVALID_JSON");
        }
        const object = data as {
          records?: unknown;
          data?: { records?: unknown };
          isSuccess?: unknown;
          error?: unknown;
        } | null;
        if (
          object &&
          "isSuccess" in Object(object) &&
          object.isSuccess !== true
        )
          throw new ApiError("INVALID_JSON");
        const records = Array.isArray(data)
          ? data
          : (object?.records ?? object?.data?.records);
        const parsed = importSchema.safeParse({ records });
        if (!parsed.success) throw new ApiError("INVALID_JSON");
        summary = await api.importRecords(parsed.data.records);
      } else summary = await api.importGroup(group.trim());
      setResult(summary);
      setOffset(0);
      list.retry();
    } catch (e) {
      if (
        e instanceof ApiError &&
        ["INVALID_JSON", "FILE_TOO_LARGE"].includes(e.code)
      )
        logEvent("file_rejected", 0, e.code);
      setError(e);
    } finally {
      busyRef.current = false;
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="Рабочее пространство / Диалоги"
        title="Каждый диалог — в деталях"
      >
        <p className="subtitle">
          Исходная переписка, контекст и правила оценки
          <br />в одном рабочем пространстве.
        </p>
      </PageTitle>
      <section className="panel import-panel" aria-labelledby="import-title">
        <div>
          <span className="section-number">01 / ИМПОРТ</span>
          <h2 id="import-title">Добавьте новые диалоги</h2>
          <p className="muted">
            Загрузите набор по группе или выберите JSON-файл.
          </p>
        </div>
        <form
          className="import-actions"
          onSubmit={(e) => {
            e.preventDefault();
            if (group.trim()) void performImport();
          }}
        >
          <label>
            Группа набора
            <input
              value={group}
              onChange={(e) => setGroup(e.target.value)}
              placeholder="Например, DEV"
              disabled={busy}
              required
            />
          </label>
          <button disabled={busy || !group.trim()}>
            {busy ? "Импортируем…" : "Импортировать группу"}
          </button>
          <span className="muted or">или</span>
          <label
            className={`button secondary file-button ${busy ? "disabled" : ""}`}
          >
            Загрузить JSON
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              aria-label="Загрузить JSON"
              disabled={busy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void performImport(file);
              }}
            />
          </label>
        </form>
      </section>
      {error != null && <ErrorNotice error={error} />}
      {result && (
        <div className="notice success" role="status">
          Импорт завершён: получено {result.received}, новых {result.created},
          новых версий {result.new_versions}, без изменений {result.unchanged}.
        </div>
      )}
      <section className="panel list-panel" aria-labelledby="list-title">
        <div className="panel-heading">
          <div>
            <span className="section-number">02 / БИБЛИОТЕКА</span>
            <h2 id="list-title">
              Диалоги <span className="count">{list.data?.total ?? "—"}</span>
            </h2>
          </div>
          <div className="filters">
            <label>
              Язык
              <select
                value={language}
                onChange={(e) => {
                  setLanguage(e.target.value as Language | "");
                  setOffset(0);
                }}
              >
                <option value="">Все языки</option>
                <option>RU</option>
                <option>KZ</option>
                <option>EN</option>
              </select>
            </label>
            <label>
              Канал
              <select
                value={channel}
                onChange={(e) => {
                  setChannel(e.target.value as Channel | "");
                  setOffset(0);
                }}
              >
                <option value="">Все каналы</option>
                <option value="agent chat">Чаты</option>
                <option value="voice call">Звонки</option>
              </select>
            </label>
            <button
              className="secondary refresh"
              onClick={list.retry}
              disabled={list.loading}
            >
              Обновить
            </button>
          </div>
        </div>
        {list.loading ? (
          <Loading />
        ) : list.error ? (
          <ErrorNotice error={list.error} retry={list.retry} />
        ) : !list.data?.items.length ? (
          <Empty title="Диалогов пока нет">
            <p className="muted">Импортируйте набор или измените фильтры.</p>
          </Empty>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Диалог</th>
                    <th>Канал</th>
                    <th>Язык</th>
                    <th>Сообщения</th>
                    <th>Группа</th>
                    <th>
                      <span className="sr-only">Открыть</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <Link
                          className="conversation-link"
                          to={`/conversations/${c.id}`}
                        >
                          {c.external_id}
                        </Link>
                        <small>
                          {c.source === "upload"
                            ? "Из JSON-файла"
                            : "Набор диалогов"}
                        </small>
                      </td>
                      <td>
                        <ChannelTag channel={c.channel} />
                      </td>
                      <td>
                        <span className="language">{c.language}</span>
                      </td>
                      <td>{c.message_count}</td>
                      <td className="group-cell">{c.group_id}</td>
                      <td>
                        <Link
                          className="open-link"
                          to={`/conversations/${c.id}`}
                          aria-label={`Открыть ${c.external_id}`}
                        >
                          <OpenArrow />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pagination">
              <span>
                {offset + 1}–{offset + list.data.items.length} из{" "}
                {list.data.total}
              </span>
              <div>
                <button
                  className="secondary"
                  disabled={!offset}
                  onClick={() => setOffset((o) => Math.max(0, o - 20))}
                >
                  Назад
                </button>
                <button
                  className="secondary"
                  disabled={offset + 20 >= list.data.total}
                  onClick={() => setOffset((o) => o + 20)}
                >
                  Далее
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </>
  );
}
