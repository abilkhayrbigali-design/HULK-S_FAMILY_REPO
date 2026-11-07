import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useApi } from "../api/context";
import { useResource } from "../api/useResource";
import {
  ChannelTag,
  ErrorNotice,
  Loading,
  PageTitle,
} from "../components/common";

const roles = {
  client: "Клиент",
  operator: "Оператор",
  bot: "Бот",
  system: "Система",
};
export function ConversationDetail() {
  const { id = "" } = useParams();
  const api = useApi();
  const conversation = useResource(
    useCallback((signal) => api.conversation(id, signal), [api, id]),
  );
  const [checklistOffset, setChecklistOffset] = useState(0);
  const checklists = useResource(
    useCallback(
      (signal) => api.checklists(checklistOffset, signal),
      [api, checklistOffset],
    ),
  );
  const [checklistId, setChecklistId] = useState("");
  const checklist = useResource(
    useCallback(
      async (signal) =>
        checklistId ? api.checklist(checklistId, signal) : null,
      [api, checklistId],
    ),
  );
  const [selectedVersion, setSelectedVersion] = useState("");
  const version =
    checklist.data?.versions.find((v) => v.id === selectedVersion) ??
    checklist.data?.current_version;
  if (conversation.loading) return <Loading />;
  if (conversation.error || !conversation.data)
    return (
      <ErrorNotice error={conversation.error} retry={conversation.retry} />
    );
  const c = conversation.data;
  return (
    <>
      <Link className="back-link" to="/conversations">
        ← Все диалоги
      </Link>
      <PageTitle eyebrow="Диалог / Исходный транскрипт" title={c.external_id}>
        <div className="inline">
          <ChannelTag channel={c.channel} />
          <span className="language">{c.language}</span>
        </div>
      </PageTitle>
      <div className="detail-grid">
        <section
          className="panel transcript"
          aria-labelledby="transcript-title"
        >
          <div className="panel-heading">
            <h2 id="transcript-title">Переписка</h2>
            <span className="muted">{c.messages.length} сообщений</span>
          </div>
          <ol className="messages">
            {c.messages.map((m) => (
              <li key={m.id} id={m.id} className={`message ${m.speaker}`}>
                <div className="message-meta">
                  <strong>{roles[m.speaker]}</strong>
                  <time>{m.timestamp}</time>
                  <span className="message-id">{m.id}</span>
                </div>
                <p>{m.text}</p>
              </li>
            ))}
          </ol>
        </section>
        <aside className="detail-aside">
          <section className="panel">
            <span className="section-number">ПРАВИЛА ОЦЕНКИ</span>
            <h2>Версия чек-листа</h2>
            {checklists.loading ? (
              <Loading />
            ) : checklists.error ? (
              <ErrorNotice error={checklists.error} retry={checklists.retry} />
            ) : (
              <>
                <label>
                  Чек-лист
                  <select
                    value={checklistId}
                    onChange={(e) => {
                      setChecklistId(e.target.value);
                      setSelectedVersion("");
                    }}
                  >
                    <option value="">Выберите чек-лист</option>
                    {checklists.data?.items.map((ch) => (
                      <option value={ch.id} key={ch.id}>
                        {ch.name}
                      </option>
                    ))}
                  </select>
                </label>
                {checklists.data?.total === 0 && (
                  <p className="muted">
                    Пока нет чек-листов.{" "}
                    <Link to="/checklists/new">Создать</Link>
                  </p>
                )}
                {(checklists.data?.total ?? 0) > 100 && (
                  <div className="pagination">
                    <button
                      className="secondary"
                      disabled={!checklistOffset}
                      onClick={() => {
                        setChecklistId("");
                        setChecklistOffset((o) => o - 100);
                      }}
                    >
                      Назад
                    </button>
                    <button
                      className="secondary"
                      disabled={checklistOffset + 100 >= checklists.data!.total}
                      onClick={() => {
                        setChecklistId("");
                        setChecklistOffset((o) => o + 100);
                      }}
                    >
                      Далее
                    </button>
                  </div>
                )}
              </>
            )}
            {checklistId &&
              (checklist.loading ? (
                <Loading />
              ) : checklist.error ? (
                <ErrorNotice error={checklist.error} retry={checklist.retry} />
              ) : (
                version && (
                  <>
                    <label>
                      Версия
                      <select
                        value={version.id}
                        onChange={(e) => setSelectedVersion(e.target.value)}
                      >
                        {checklist.data!.versions.map((v) => (
                          <option key={v.id} value={v.id}>
                            Версия {v.version_number}
                            {v.id === checklist.data!.current_version.id
                              ? " · актуальная"
                              : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    <p className="muted">
                      Критериев: {version.criteria.length}. Выбранная версия
                      сохранена и неизменяема.
                    </p>
                    <Link to={`/checklists/${checklistId}`}>
                      Открыть редактор ↗
                    </Link>
                  </>
                )
              ))}
            <div className="notice subtle">
              AI-анализ ещё не подключён. Здесь доступен просмотр транскрипта и
              выбор правил.
            </div>
          </section>
          {c.channel === "voice call" && (
            <section className="panel">
              <span className="section-number">ЗАПИСЬ ЗВОНКА</span>
              <h2>Аудио</h2>
              {c.audio_url ? (
                <>
                  <p className="muted">
                    Запись откроется отдельно. Время в транскрипте показано без
                    преобразования.
                  </p>
                  <a
                    className="button secondary"
                    href={c.audio_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    referrerPolicy="no-referrer"
                  >
                    Открыть запись ↗
                  </a>
                </>
              ) : (
                <p className="muted">Аудиоссылка не предоставлена.</p>
              )}
            </section>
          )}
        </aside>
      </div>
    </>
  );
}
