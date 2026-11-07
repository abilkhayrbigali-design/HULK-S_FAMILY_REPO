import type {
  Assessment,
  ChecklistVersion,
  CompletedEvaluation,
  Conversation,
  Criterion,
  CriterionAssessment,
  CriterionResult,
  Evidence,
} from "../api/schema";

export type ShowEvidence = (evidence: Evidence) => void;
export const evidenceBounds = (item: Evidence) =>
  item.kind === "message"
    ? [item.message_id, item.message_id]
    : [item.start_message_id, item.end_message_id];

export function evidenceIndices(
  messages: Conversation["messages"],
  item: Evidence,
) {
  const [start, end] = evidenceBounds(item);
  const first = messages.findIndex((m) => m.id === start);
  const last = messages.findIndex((m) => m.id === end);
  return first >= 0 && last >= first ? { first, last } : null;
}

export function EvidenceLinks({
  evidence,
  show,
}: {
  evidence: Evidence[];
  show?: ShowEvidence;
}) {
  return (
    <ul className="evidence-list" aria-label="Доказательства">
      {evidence.map((item, index) => {
        const [start, end] = evidenceBounds(item);
        const label =
          item.kind === "message"
            ? `Реплика ${start}`
            : `Диапазон ${start}–${end}`;
        return (
          <li key={index}>
            {show ? (
              <button
                type="button"
                className="secondary"
                onClick={() => show(item)}
              >
                {label}
              </button>
            ) : (
              <span>{label}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

const points = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

function score(total: number | null) {
  return total === null
    ? "Нет применимых весов"
    : `${points.format(total)} / 100`;
}

export function CriterionAnswer({
  result,
  criterion,
  show,
  explain = true,
}: {
  result: CriterionResult;
  criterion?: Criterion;
  show?: ShowEvidence;
  explain?: boolean;
}) {
  const { answer } = result;
  const description = criterion?.scale?.find(
    (step) => step.value === answer,
  )?.description;
  return (
    <>
      <p>
        <strong>
          {result.applicability === "not_applicable"
            ? "N/A · Не применимо"
            : answer === true
              ? "Да"
              : answer === false
                ? "Нет"
                : answer}
        </strong>
        {description && ` · ${description}`}
      </p>
      {explain && <p>{result.explanation}</p>}
      {result.review_reasons.map((reason, index) => (
        <p className="muted" key={index}>
          {reason}
        </p>
      ))}
      <EvidenceLinks evidence={result.evidence} show={show} />
    </>
  );
}

function ScoreSummary({ title, value }: { title: string; value: Assessment }) {
  return (
    <div className="score-summary" aria-label={title}>
      <h3>{title}</h3>
      <strong>{score(value.total)}</strong>
      {value.no_applicable_reason && <p>{value.no_applicable_reason}</p>}
      <p>
        Грубые нарушения:{" "}
        {value.gross_error === null
          ? "критерий отсутствует"
          : value.gross_error
            ? "обнаружены"
            : "не обнаружены"}
      </p>
      {value.sections.length > 0 && (
        <dl className="section-scores">
          {value.sections.map((section) => (
            <div key={section.name}>
              <dt>{section.name ?? "Без секции"}</dt>
              <dd>{score(section.total)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function ScoredAnswer({
  title,
  result,
  criterion,
  show,
}: {
  title: string;
  result: CriterionAssessment;
  criterion?: Criterion;
  show?: ShowEvidence;
}) {
  return (
    <div>
      <h4>{title}</h4>
      <CriterionAnswer result={result} criterion={criterion} show={show} />
      {criterion?.type !== "flag" && (
        <p className="muted">
          Вклад: {points.format(result.effective_points)} · Вес:{" "}
          {points.format(result.effective_weight)}
        </p>
      )}
    </div>
  );
}

export function EvaluationResult({
  evaluation,
  version,
  show,
}: {
  evaluation: CompletedEvaluation;
  version?: ChecklistVersion;
  show?: ShowEvidence;
}) {
  const definitions = new Map(
    version?.criteria.map((criterion) => [criterion.id, criterion]),
  );
  const final = new Map(
    evaluation.final.criteria.map((row) => [row.criterion_id, row]),
  );
  return (
    <>
      {version && (
        <p className="muted">
          {version.name} · Версия {version.version_number}
        </p>
      )}
      <div className="comparison-grid">
        <ScoreSummary title="Исходная оценка" value={evaluation.original} />
        <ScoreSummary title="Финальная оценка" value={evaluation.final} />
      </div>
      {evaluation.review_required && (
        <div className="notice subtle">
          <div>
            <strong>Требуется ручная проверка</strong>
            <ul>
              {evaluation.review_reasons.map((reason, index) => (
                <li key={index}>{reason}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
      <h3>Критерии</h3>
      {evaluation.original.criteria.map((original) => {
        const criterion = definitions.get(original.criterion_id);
        const current = final.get(original.criterion_id);
        return (
          <article className="criterion-result" key={original.criterion_id}>
            <h4>{criterion?.name ?? original.criterion_id}</h4>
            <div className="comparison-grid">
              <ScoredAnswer
                title="Исходный ответ"
                result={original}
                criterion={criterion}
                show={show}
              />
              {current && (
                <ScoredAnswer
                  title="Финальный ответ"
                  result={current}
                  criterion={criterion}
                  show={show}
                />
              )}
            </div>
          </article>
        );
      })}
    </>
  );
}
