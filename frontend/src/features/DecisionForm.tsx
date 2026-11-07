import { useEffect, useRef, useState, type FormEvent } from "react";
import { useApi } from "../api/context";
import { ApiError, logEvent } from "../api/errors";
import type {
  Appeal,
  Conversation,
  Criterion,
  DecisionInput,
  Evaluation,
  Evidence,
  OpenAppeal,
} from "../api/schema";
import { useMutation } from "../api/useMutation";
import { ErrorNotice } from "../components/common";
import {
  CriterionAnswer,
  evidenceBounds,
  evidenceIndices,
  type ShowEvidence,
} from "./EvaluationResult";

type Messages = Conversation["messages"];

function EvidenceEditor({
  rows,
  messages,
  disabled,
  change,
}: {
  rows: Evidence[];
  messages: Messages;
  disabled: boolean;
  change: (rows: Evidence[]) => void;
}) {
  function replace(index: number, row: Evidence) {
    change(rows.map((item, i) => (i === index ? row : item)));
  }
  const options = messages.map((m) => (
    <option key={m.id} value={m.id}>
      {m.id} · {m.timestamp}
    </option>
  ));
  return (
    <fieldset className="evidence-editor" disabled={disabled}>
      <legend>Доказательства решения</legend>
      {rows.map((row, index) => {
        const [first, last] = evidenceBounds(row);
        return (
          <div className="evidence-row" key={index}>
            <label>
              Вид доказательства {index + 1}
              <select
                value={row.kind}
                onChange={(event) =>
                  replace(
                    index,
                    event.target.value === "range"
                      ? {
                          kind: "range",
                          start_message_id: first,
                          end_message_id: last,
                        }
                      : { kind: "message", message_id: first },
                  )
                }
              >
                <option value="message">Реплика</option>
                <option value="range">Диапазон</option>
              </select>
            </label>
            <label>
              {row.kind === "message" ? "Реплика" : "Начало диапазона"}{" "}
              {index + 1}
              <select
                required
                value={first}
                onChange={(event) =>
                  replace(
                    index,
                    row.kind === "message"
                      ? { ...row, message_id: event.target.value }
                      : { ...row, start_message_id: event.target.value },
                  )
                }
              >
                <option value="">Выберите реплику</option>
                {options}
              </select>
            </label>
            {row.kind === "range" && (
              <label>
                Конец диапазона {index + 1}
                <select
                  required
                  value={last}
                  onChange={(event) =>
                    replace(index, {
                      ...row,
                      end_message_id: event.target.value,
                    })
                  }
                >
                  <option value="">Выберите реплику</option>
                  {options}
                </select>
              </label>
            )}
            <button
              type="button"
              className="secondary"
              disabled={rows.length <= 1}
              onClick={() => change(rows.filter((_, i) => i !== index))}
            >
              Удалить доказательство {index + 1}
            </button>
          </div>
        );
      })}
      <button
        type="button"
        className="secondary"
        onClick={() => change([...rows, { kind: "message", message_id: "" }])}
      >
        Добавить доказательство
      </button>
    </fieldset>
  );
}

export function DecisionForm({
  appeal,
  action,
  criterion,
  messages,
  show,
  close,
  onRefresh,
  onSaved,
}: {
  appeal: OpenAppeal;
  action: DecisionInput["action"];
  criterion?: Criterion;
  messages?: Messages;
  show?: ShowEvidence;
  close: () => void;
  onRefresh: (evaluation: Evaluation) => void;
  onSaved: (evaluation: Evaluation) => void;
}) {
  const api = useApi();
  const submission = useMutation();
  const reload = useMutation();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const [expectedRevision, setExpectedRevision] = useState(
    appeal.current_revision,
  );
  const [reason, setReason] = useState("");
  const [answer, setAnswer] = useState(String(appeal.current.answer ?? ""));
  const [applicability, setApplicability] = useState(
    appeal.current.applicability,
  );
  const [evidence, setEvidence] = useState(appeal.current.evidence);
  const [invalid, setInvalid] = useState("");
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<Appeal>();
  const displayed = latest ?? appeal;

  async function refreshConflict() {
    const result = await reload.submit(async (signal) => {
      const [currentAppeal, evaluation] = await Promise.all([
        api.appeal(appeal.id, signal),
        api.evaluation(appeal.evaluation_id, signal),
      ]);
      if (
        currentAppeal.id !== appeal.id ||
        currentAppeal.evaluation_id !== evaluation.id ||
        evaluation.id !== appeal.evaluation_id
      ) {
        logEvent("load_failed", 0, "INVALID_RESPONSE");
        throw new ApiError("INVALID_RESPONSE");
      }
      return { appeal: currentAppeal, evaluation };
    });
    if (result?.ok) {
      setLatest(result.data.appeal);
      onRefresh(result.data.evaluation);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (conflict || !reason.trim() || displayed.status !== "open") return;
    const base = { reason: reason.trim(), expected_revision: expectedRevision };
    let payload: DecisionInput = { ...base, action: "confirm" };
    if (action === "override") {
      if (!messages || !criterion) return;
      const value =
        applicability === "not_applicable" && criterion.allow_na
          ? null
          : criterion.type === "scale"
            ? criterion.scale?.find((step) => String(step.value) === answer)
                ?.value
            : answer === "true"
              ? true
              : answer === "false"
                ? false
                : undefined;
      if (
        value === undefined ||
        !evidence.length ||
        evidence.some((item) => !evidenceIndices(messages, item))
      ) {
        setInvalid(
          "Выберите допустимый ответ и укажите доказательства в порядке реплик.",
        );
        logEvent("form_rejected", 0, "FORM_INVALID");
        return;
      }
      payload = { ...base, action, applicability, answer: value, evidence };
    }
    setInvalid("");
    const result = await submission.submit((signal) =>
      api.decideAppeal(appeal.id, payload, signal),
    );
    if (result?.ok) onSaved(result.data.evaluation);
    else if (
      result &&
      result.error instanceof ApiError &&
      result.error.status === 409
    ) {
      setLatest(undefined);
      setConflict(true);
      await refreshConflict();
    }
  }

  return (
    <form
      className="review-form criterion-result"
      onSubmit={(event) => void submit(event)}
    >
      <h3 ref={heading} tabIndex={-1}>
        {action === "confirm" ? "Подтверждение ответа" : "Изменение ответа"} ·{" "}
        {criterion?.name ?? appeal.criterion_id}
      </h3>
      <p className="appeal-comment">Комментарий: {displayed.comment}</p>
      <div className="comparison-grid">
        <div>
          <h4>Исходный ответ</h4>
          <CriterionAnswer
            result={displayed.original}
            criterion={criterion}
            show={show}
          />
        </div>
        <div>
          <h4>
            Текущий финальный ответ · ревизия {displayed.current_revision}
          </h4>
          <CriterionAnswer
            result={displayed.current}
            criterion={criterion}
            show={show}
          />
        </div>
      </div>
      {conflict && (
        <div className="notice error" role="alert">
          <div>
            <p>
              Конфликт ревизии. Состояние оценки или апелляции изменилось.
              Решение не сохранено.
            </p>
            {latest?.status === "resolved" ? (
              <p>
                Апелляция уже решена. Сохранённое решение доступно в истории.
              </p>
            ) : latest ? (
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setExpectedRevision(latest.current_revision);
                  setConflict(false);
                }}
              >
                Продолжить с ревизией {latest.current_revision}
              </button>
            ) : reload.busy ? (
              <p>Загружаем актуальное состояние…</p>
            ) : (
              <button
                type="button"
                className="secondary"
                onClick={() => void refreshConflict()}
              >
                Загрузить актуальное состояние
              </button>
            )}
          </div>
        </div>
      )}
      {reload.error instanceof Error && <ErrorNotice error={reload.error} />}
      <label>
        Причина решения
        <textarea
          required
          disabled={submission.busy}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      {action === "override" && messages && criterion && (
        <>
          {criterion.allow_na && (
            <label>
              Применимость
              <select
                disabled={submission.busy}
                value={applicability}
                onChange={(event) =>
                  setApplicability(
                    event.target.value === "not_applicable"
                      ? "not_applicable"
                      : "applicable",
                  )
                }
              >
                <option value="applicable">Применимо</option>
                <option value="not_applicable">N/A · Не применимо</option>
              </select>
            </label>
          )}
          {applicability === "applicable" && (
            <label>
              Новый ответ
              <select
                required
                disabled={submission.busy}
                value={answer}
                onChange={(event) => setAnswer(event.target.value)}
              >
                <option value="">Выберите ответ</option>
                {criterion.type === "scale" ? (
                  criterion.scale?.map((step) => (
                    <option key={step.value} value={step.value}>
                      {step.value} · {step.description}
                    </option>
                  ))
                ) : (
                  <>
                    <option value="true">Да</option>
                    <option value="false">Нет</option>
                  </>
                )}
              </select>
            </label>
          )}
          <EvidenceEditor
            rows={evidence}
            messages={messages}
            disabled={submission.busy}
            change={setEvidence}
          />
        </>
      )}
      {invalid && (
        <p className="notice error" role="alert">
          {invalid}
        </p>
      )}
      {submission.error instanceof ApiError &&
        submission.error.status !== 409 && (
          <ErrorNotice error={submission.error} />
        )}
      <div className="inline">
        <button
          type="submit"
          disabled={
            submission.busy ||
            conflict ||
            !reason.trim() ||
            displayed.status !== "open" ||
            (action === "override" && (!messages || !criterion))
          }
        >
          Сохранить решение
        </button>
        <button
          className="secondary"
          type="button"
          disabled={submission.busy || reload.busy}
          onClick={close}
        >
          Закрыть форму решения
        </button>
      </div>
    </form>
  );
}
