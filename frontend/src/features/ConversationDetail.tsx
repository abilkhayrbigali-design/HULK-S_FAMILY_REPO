import { useCallback, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useApi } from "../api/context";
import { ApiError, safeCode } from "../api/errors";
import type { Evidence } from "../api/schema";
import { useMutation } from "../api/useMutation";
import { useResource } from "../api/useResource";
import {
  ChannelTag,
  ErrorNotice,
  Loading,
  PageTitle,
} from "../components/common";
import { EvaluationResult, evidenceIndices } from "./EvaluationResult";
import { Appeals } from "./Appeals";
import { isPending, useEvaluation } from "./useEvaluation";

const roles = {
  client: "Клиент",
  operator: "Оператор",
  bot: "Бот",
  system: "Система",
};
const statuses = {
  queued: "В очереди",
  running: "Оценка выполняется",
  completed: "Оценка завершена",
  failed: "Оценка не выполнена",
};

export function ConversationDetail() {
  const { id = "" } = useParams();
  return <ConversationWorkspace key={id} id={id} />;
}

function ConversationWorkspace({ id }: { id: string }) {
  const api = useApi();
  const [search, setSearch] = useSearchParams();
  const conversation = useResource(
    useCallback((signal) => api.conversation(id, signal), [api, id]),
  );
  const evaluationId =
    search.get("evaluation") || conversation.data?.latest_evaluation?.id || "";
  const evaluation = useEvaluation(id, evaluationId);
  const run = evaluation.data;
  const requestedTranscript = run?.transcript_version_id;
  const currentTranscript = conversation.data?.transcript_version_id;
  const historical =
    !!requestedTranscript &&
    !!currentTranscript &&
    requestedTranscript !== currentTranscript;
  const historicalTranscript = useResource(
    useCallback(
      async (signal) => {
        if (!historical) return null;
        const result = await api.conversation(id, signal, requestedTranscript);
        if (
          result.id !== id ||
          result.transcript_version_id !== requestedTranscript
        )
          throw new ApiError("INVALID_RESPONSE");
        return result;
      },
      [api, id, requestedTranscript, historical],
    ),
  );
  const checklistVersionId =
    run?.status === "completed" ? run.checklist_version_id : undefined;
  const resultVersion = useResource(
    useCallback(
      async (signal) => {
        if (!checklistVersionId) return null;
        const result = await api.version(checklistVersionId, signal);
        if (result.id !== checklistVersionId)
          throw new ApiError("INVALID_RESPONSE");
        return result;
      },
      [api, checklistVersionId],
    ),
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
  const selectedChecklist =
    checklist.data?.id === checklistId ? checklist.data : undefined;
  const version =
    selectedChecklist?.versions.find((v) => v.id === selectedVersion) ??
    selectedChecklist?.current_version;
  const start = useMutation();
  const [highlight, setHighlight] = useState<{
    id: string;
    first: number;
    last: number;
  }>();
  const [evidenceError, setEvidenceError] = useState<string>();
  if (conversation.loading) return <Loading />;
  if (conversation.error || !conversation.data)
    return (
      <ErrorNotice error={conversation.error} retry={conversation.retry} />
    );
  const c = conversation.data;
  const transcript = historical
    ? historicalTranscript.data?.transcript_version_id === requestedTranscript
      ? historicalTranscript.data
      : undefined
    : c;
  const currentHighlight = highlight?.id === run?.id ? highlight : undefined;
  const pending = !!evaluationId && (!run || isPending(run.status));
  const uncertainStart =
    start.error instanceof ApiError &&
    ["NETWORK_ERROR", "TIMEOUT", "INVALID_RESPONSE"].includes(start.error.code);
  const pinnedVersion =
    resultVersion.data?.id === checklistVersionId
      ? resultVersion.data
      : undefined;

  function selectRun(id?: string) {
    const next = new URLSearchParams(search);
    if (id) next.set("evaluation", id);
    else next.delete("evaluation");
    setSearch(next, { replace: true });
  }

  async function startEvaluation() {
    if (!version || pending || uncertainStart) return;
    const result = await start.submit((signal) =>
      api.startEvaluation(c.id, version.id, signal),
    );
    if (result?.ok) selectRun(result.data.id);
  }

  function readLatest() {
    start.clearError();
    selectRun();
    conversation.retry();
  }

  function showEvidence(item: Evidence) {
    if (!transcript || !run) return;
    const indices = evidenceIndices(transcript.messages, item);
    const target =
      indices &&
      document.getElementById(
        `transcript-message-${transcript.messages[indices.first].id}`,
      );
    if (!indices || !target) {
      setEvidenceError(run.id);
      return;
    }
    setEvidenceError(undefined);
    setHighlight({ id: run.id, ...indices });
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "center" });
  }
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
            {transcript && (
              <span className="muted">
                {transcript.messages.length} сообщений
              </span>
            )}
          </div>
          {historical && (
            <p className="transcript-note">
              Показана версия транскрипта, сохранённая для этой оценки.
            </p>
          )}
          {historical && historicalTranscript.error ? (
            <ErrorNotice
              error={historicalTranscript.error}
              retry={historicalTranscript.retry}
            />
          ) : !transcript ? (
            <Loading />
          ) : (
            <ol className="messages">
              {transcript.messages.map((m, index) => {
                const selected =
                  currentHighlight &&
                  index >= currentHighlight.first &&
                  index <= currentHighlight.last;
                return (
                  <li
                    key={m.id}
                    id={`transcript-message-${m.id}`}
                    tabIndex={-1}
                    className={`message ${m.speaker}${selected ? " evidence-target" : ""}`}
                  >
                    <div className="message-meta">
                      <strong>{roles[m.speaker]}</strong>
                      <time>{m.timestamp}</time>
                      <span className="message-id">{m.id}</span>
                    </div>
                    <p>{m.text}</p>
                    {selected && index === currentHighlight?.first && (
                      <a href="#evaluation-title">К результату оценки</a>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
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
                        {selectedChecklist?.versions.map((v) => (
                          <option key={v.id} value={v.id}>
                            Версия {v.version_number}
                            {v.id === selectedChecklist.current_version.id
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
            <div className="evaluation-launch">
              <button
                type="button"
                disabled={!version || pending || start.busy || uncertainStart}
                onClick={() => void startEvaluation()}
              >
                {start.busy ? "Запускаем…" : "Запустить оценку"}
              </button>
              {start.error instanceof ApiError && (
                <ErrorNotice error={start.error} />
              )}
              {uncertainStart && (
                <button
                  type="button"
                  className="secondary"
                  onClick={readLatest}
                >
                  Проверить последний запуск
                </button>
              )}
              {run && (
                <div>
                  <p role="status" className="evaluation-status">
                    {statuses[run.status]}
                  </p>
                  <a href="#evaluation-title">Перейти к оценке</a>
                </div>
              )}
            </div>
          </section>
          {transcript?.channel === "voice call" && (
            <section className="panel">
              <span className="section-number">ЗАПИСЬ ЗВОНКА</span>
              <h2>Аудио</h2>
              {transcript.audio_url ? (
                <>
                  <p className="muted">
                    Запись откроется отдельно. Время в транскрипте показано без
                    преобразования.
                  </p>
                  <a
                    className="button secondary"
                    href={transcript.audio_url}
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
      <section
        className="panel evaluation-panel"
        aria-labelledby="evaluation-title"
      >
        <h2 id="evaluation-title" tabIndex={-1}>
          Оценка диалога
        </h2>
        {!evaluationId && (
          <p className="muted">Запусков пока нет. Выберите версию чек-листа.</p>
        )}
        {evaluationId && !run && !evaluation.error && <Loading />}
        {evaluation.error instanceof Error && (
          <ErrorNotice error={evaluation.error} retry={evaluation.retry} />
        )}
        {search.has("evaluation") && evaluation.error instanceof Error && (
          <button type="button" className="secondary" onClick={readLatest}>
            Проверить последний запуск
          </button>
        )}
        {run?.status === "failed" && (
          <ErrorNotice error={new ApiError(safeCode(run.error.code, 500))} />
        )}
        {run?.status === "completed" && (
          <>
            {resultVersion.error instanceof Error && (
              <ErrorNotice
                error={resultVersion.error}
                retry={resultVersion.retry}
              />
            )}
            {evidenceError === run.id && (
              <p className="notice error" role="alert">
                Доказательство отсутствует в сохранённой версии транскрипта.
              </p>
            )}
            <EvaluationResult
              evaluation={run}
              version={pinnedVersion ?? undefined}
              show={transcript ? showEvidence : undefined}
            />
          </>
        )}
      </section>
      {run?.status === "completed" && (
        <Appeals
          key={run.id}
          evaluation={run}
          version={pinnedVersion ?? undefined}
          messages={transcript?.messages}
          show={transcript ? showEvidence : undefined}
          update={evaluation.update}
        />
      )}
    </>
  );
}
