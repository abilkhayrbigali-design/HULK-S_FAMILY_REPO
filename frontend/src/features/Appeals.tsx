import { useCallback, useState, type FormEvent } from "react";
import { useApi } from "../api/context";
import { ApiError, logEvent } from "../api/errors";
import type {
  Appeal,
  ChecklistVersion,
  CompletedEvaluation,
  Conversation,
  Criterion,
  DecisionInput,
  Evaluation,
  OpenAppeal,
} from "../api/schema";
import { useMutation } from "../api/useMutation";
import { useResource } from "../api/useResource";
import { ErrorNotice, Loading } from "../components/common";
import { DecisionForm } from "./DecisionForm";
import { CriterionAnswer, type ShowEvidence } from "./EvaluationResult";

type Review = { appeal: OpenAppeal; action: DecisionInput["action"] };
const actionLabel = {
  confirm: "Подтвердить ответ",
  override: "Изменить ответ",
};

function AppealEntry({
  appeal,
  criterion,
  show,
  canOverride,
  canReview,
  onReview,
}: {
  appeal: Appeal;
  criterion?: Criterion;
  show?: ShowEvidence;
  canOverride: boolean;
  canReview: boolean;
  onReview: (review: Review) => void;
}) {
  return (
    <article className="criterion-result">
      <h3>{criterion?.name ?? appeal.criterion_id}</h3>
      <p className="muted">
        {appeal.status === "open" ? "Открыта" : "Решена"} · Ревизия при подаче:{" "}
        {appeal.base_revision}
      </p>
      <p className="appeal-comment">{appeal.comment}</p>
      {appeal.status === "resolved" ? (
        <>
          <strong>
            {appeal.decision.action === "confirm"
              ? "Ответ подтверждён"
              : "Ответ изменён"}
          </strong>
          <p className="appeal-comment">
            Причина решения: {appeal.decision.reason}
          </p>
          <CriterionAnswer
            result={appeal.decision.result}
            criterion={criterion}
            show={show}
            explain={
              appeal.decision.result.explanation !== appeal.decision.reason
            }
          />
        </>
      ) : (
        <div className="inline" aria-label="Действия проверяющего">
          <button
            className="secondary"
            type="button"
            disabled={!canReview}
            onClick={() => onReview({ appeal, action: "confirm" })}
          >
            {actionLabel.confirm}
          </button>
          <button
            className="secondary"
            type="button"
            disabled={!canOverride || !canReview}
            onClick={() => onReview({ appeal, action: "override" })}
          >
            {actionLabel.override}
          </button>
        </div>
      )}
    </article>
  );
}

export function Appeals({
  evaluation,
  version,
  messages,
  show,
  update,
}: {
  evaluation: CompletedEvaluation;
  version?: ChecklistVersion;
  messages?: Conversation["messages"];
  show?: ShowEvidence;
  update: (evaluation: Evaluation) => void;
}) {
  const api = useApi();
  const { id } = evaluation;
  const [offset, setOffset] = useState(0);
  const history = useResource(
    useCallback((signal) => api.appeals(id, offset, signal), [api, id, offset]),
  );
  const [criterionId, setCriterionId] = useState("");
  const [comment, setComment] = useState("");
  const [review, setReview] = useState<Review>();
  const [notice, setNotice] = useState("");
  const submission = useMutation();
  const definitions = new Map(
    version?.criteria.map((criterion) => [criterion.id, criterion]),
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!criterionId || !comment.trim()) {
      logEvent("form_rejected", 0, "FORM_INVALID");
      return;
    }
    setNotice("");
    const result = await submission.submit((signal) =>
      api.createAppeal(id, criterionId, comment.trim(), signal),
    );
    if (result?.ok) {
      setCriterionId("");
      setComment("");
      setNotice("Апелляция отправлена.");
      if (offset) setOffset(0);
      else history.retry();
    } else if (
      result &&
      result.error instanceof ApiError &&
      result.error.status === 409
    ) {
      history.retry();
    }
  }

  return (
    <section className="panel evaluation-panel" aria-labelledby="appeals-title">
      <h2 id="appeals-title">Апелляции</h2>
      <form className="review-form" onSubmit={(event) => void submit(event)}>
        <h3>Подать апелляцию</h3>
        <label>
          Критерий для апелляции
          <select
            required
            disabled={submission.busy}
            value={criterionId}
            onChange={(event) => setCriterionId(event.target.value)}
          >
            <option value="">Выберите критерий</option>
            {evaluation.original.criteria.map((criterion) => (
              <option
                value={criterion.criterion_id}
                key={criterion.criterion_id}
              >
                {definitions.get(criterion.criterion_id)?.name ??
                  criterion.criterion_id}
              </option>
            ))}
          </select>
        </label>
        <label>
          Комментарий к апелляции
          <textarea
            required
            disabled={submission.busy}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
        </label>
        <button
          disabled={submission.busy || !criterionId || !comment.trim()}
          type="submit"
        >
          Отправить апелляцию
        </button>
        {submission.error instanceof ApiError &&
          (submission.error.status === 409 ? (
            <p role="alert" className="notice error">
              По этому критерию уже есть открытая апелляция. Проверьте историю.
            </p>
          ) : (
            <ErrorNotice error={submission.error} />
          ))}
      </form>
      {notice && (
        <p role="status" className="notice subtle">
          {notice}
        </p>
      )}
      <h3>История апелляций</h3>
      {history.loading ? (
        <Loading />
      ) : history.error ? (
        <ErrorNotice error={history.error} retry={history.retry} />
      ) : history.data?.items.length === 0 ? (
        <p className="muted">Апелляций пока нет.</p>
      ) : (
        history.data?.items.map((appeal) => (
          <AppealEntry
            key={appeal.id}
            appeal={appeal}
            criterion={definitions.get(appeal.criterion_id)}
            show={show}
            canOverride={!!messages && definitions.has(appeal.criterion_id)}
            canReview={!review}
            onReview={setReview}
          />
        ))
      )}
      {history.data && history.data.total > history.data.limit && (
        <div className="pagination">
          <button
            type="button"
            className="secondary"
            disabled={offset === 0}
            onClick={() => setOffset((value) => value - 50)}
          >
            Назад
          </button>
          <button
            type="button"
            className="secondary"
            disabled={offset + history.data.limit >= history.data.total}
            onClick={() => setOffset((value) => value + 50)}
          >
            Далее
          </button>
        </div>
      )}
      {review && (
        <DecisionForm
          key={`${review.appeal.id}:${review.action}`}
          appeal={review.appeal}
          action={review.action}
          criterion={definitions.get(review.appeal.criterion_id)}
          messages={messages}
          show={show}
          close={() => setReview(undefined)}
          onRefresh={(result) => {
            update(result);
            history.retry();
          }}
          onSaved={(result) => {
            update(result);
            setReview(undefined);
            setNotice("Решение сохранено.");
            history.retry();
          }}
        />
      )}
    </section>
  );
}
