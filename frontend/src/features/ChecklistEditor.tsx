import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useBlocker, useNavigate, useParams } from "react-router-dom";
import { useApi } from "../api/context";
import { useResource } from "../api/useResource";
import { ApiError, logEvent } from "../api/errors";
import {
  criteriaSchema,
  type ChecklistDetail,
  type ChecklistVersion,
  type Criterion,
} from "../api/schema";
import {
  ConfirmDialog,
  ErrorNotice,
  Loading,
  PageTitle,
} from "../components/common";

type Draft = Omit<Criterion, "weight" | "scale" | "na_rule"> & {
  weight: string;
  na_rule: string;
  scale: { value: string; description: string }[];
};
const fromCriterion = (c: Criterion): Draft => ({
  ...c,
  weight: String(c.weight),
  na_rule: c.na_rule ?? "",
  scale: (c.scale ?? []).map((s) => ({ ...s, value: String(s.value) })),
});
const newCriterion = (): Draft => ({
  id: crypto.randomUUID(),
  name: "",
  instruction: "",
  type: "binary",
  weight: "10",
  allow_na: false,
  na_rule: "",
  scale: [],
  section: null,
});
const parseNumber = (value: string) =>
  value.trim() === "" ? NaN : Number(value);
const toCriterion = (c: Draft) => ({
  ...c,
  weight: parseNumber(c.weight),
  na_rule: c.allow_na ? c.na_rule.trim() : null,
  section: c.section?.trim() || null,
  scale:
    c.type === "scale"
      ? c.scale.map((s) => ({
          value: parseNumber(s.value),
          description: s.description,
        }))
      : null,
});

export function ChecklistEditor() {
  const { id } = useParams();
  const api = useApi();
  const resource = useResource(
    useCallback(
      async (signal) => (id ? api.checklist(id, signal) : null),
      [api, id],
    ),
  );
  if (resource.loading) return <Loading />;
  if (resource.error)
    return <ErrorNotice error={resource.error} retry={resource.retry} />;
  return <EditorForm key={id ?? "new"} initial={resource.data ?? undefined} />;
}
function EditorForm({ initial }: { initial?: ChecklistDetail }) {
  const api = useApi();
  const navigate = useNavigate();
  const [name, setName] = useState(initial?.name ?? "");
  const [criteria, setCriteria] = useState<Draft[]>(
    initial?.current_version.criteria.map(fromCriterion) ?? [newCriterion()],
  );
  const [expected, setExpected] = useState(
    initial?.current_version.version_number ?? 0,
  );
  const [viewedVersion, setViewedVersion] = useState(
    initial?.current_version.id ?? "",
  );
  const [history, setHistory] = useState(initial?.versions ?? []);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const allowNavigation = useRef(false);
  const [error, setError] = useState<unknown>();
  const [validation, setValidation] = useState<string[]>([]);
  const [success, setSuccess] = useState("");
  const [latestError, setLatestError] = useState<unknown>();
  const [latest, setLatest] = useState<ChecklistDetail>();
  const [conflictLoading, setConflictLoading] = useState(false);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const blocker = useBlocker(() => (dirty || busy) && !allowNavigation.current);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty || busy) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty, busy]);
  function edit(index: number, patch: Partial<Draft>) {
    setCriteria((cs) =>
      cs.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    );
    setDirty(true);
    setSuccess("");
  }
  function changeType(index: number, type: Criterion["type"]) {
    const c = criteria[index];
    edit(index, {
      type,
      weight: type === "flag" ? "0" : Number(c.weight) > 0 ? c.weight : "10",
      allow_na: type === "flag" ? false : c.allow_na,
      na_rule: type === "flag" ? "" : c.na_rule,
      scale:
        type === "scale"
          ? c.scale.length
            ? c.scale
            : [
                { value: "0", description: "" },
                { value: "10", description: "" },
              ]
          : [],
    });
  }
  async function save() {
    if (busyRef.current) return;
    const parsed = criteriaSchema.safeParse(criteria.map(toCriterion));
    const problems: string[] = [];
    if (!name.trim()) problems.push("Укажите название чек-листа.");
    if (!parsed.success)
      for (const issue of parsed.error.issues) {
        const index =
          typeof issue.path[0] === "number" ? issue.path[0] + 1 : null;
        const field = issue.path.includes("weight")
          ? "Вес: введите конечное число больше нуля (для флага — 0)."
          : issue.path.includes("scale")
            ? "Шкала: нужны разные неотрицательные ступени и описание каждой; максимум больше нуля."
            : issue.path.includes("na_rule")
              ? "Укажите условие применения N/A."
              : issue.path.includes("name")
                ? "Укажите название критерия."
                : issue.path.includes("instruction")
                  ? "Заполните инструкцию."
                  : "Добавьте хотя бы один балльный критерий; ID должны быть уникальны.";
        problems.push(`${index ? `Критерий ${index}. ` : ""}${field}`);
      }
    if (problems.length || !parsed.success) {
      setValidation([...new Set(problems)]);
      logEvent("form_rejected", 0, "FORM_INVALID");
      return;
    }
    setValidation([]);
    setError(undefined);
    setSuccess("");
    busyRef.current = true;
    setBusy(true);
    try {
      const version = initial
        ? await api.saveVersion(initial.id, expected, parsed.data)
        : await api.createChecklist(name.trim(), parsed.data);
      setDirty(false);
      setLatest(undefined);
      setExpected(version.version_number);
      setViewedVersion(version.id);
      setCriteria(version.criteria.map(fromCriterion));
      setHistory((h) => [...h, version]);
      if (!initial) {
        allowNavigation.current = true;
        navigate(`/checklists/${version.checklist_id}`, { replace: true });
      } else
        setSuccess(
          `Версия ${version.version_number} сохранена. Предыдущие версии не изменены.`,
        );
    } catch (e) {
      setError(e);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function loadLatest() {
    if (!initial || conflictLoading) return;
    setConflictLoading(true);
    setLatestError(undefined);
    try {
      setLatest(await api.checklist(initial.id));
    } catch (e) {
      setLatestError(e);
    } finally {
      setConflictLoading(false);
    }
  }
  function replaceWith(version: ChecklistVersion) {
    setCriteria(version.criteria.map(fromCriterion));
    setViewedVersion(version.id);
    setValidation([]);
    setSuccess("");
    setDirty(version.version_number !== expected);
  }
  return (
    <>
      <Link className="back-link" to="/checklists">
        ← Все чек-листы
      </Link>
      <PageTitle
        eyebrow="Правила / Редактор"
        title={initial ? name : "Новый чек-лист"}
      >
        <span className="tag">
          {initial ? `Основа: версия ${expected}` : "Первая версия"}
        </span>
      </PageTitle>
      <p className="intro">
        Оценивается работа оператора. Добавляйте собственные критерии — каждый
        новый набор правил сохраняется отдельной версией.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
        noValidate
      >
        {validation.length > 0 && (
          <div className="notice error" role="alert">
            <div>
              <strong>Проверьте поля</strong>
              <ul>
                {validation.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          </div>
        )}
        {error != null && <ErrorNotice error={error} />}
        {error instanceof ApiError &&
          error.code === "VERSION_CONFLICT" &&
          !latest && (
            <button
              type="button"
              className="secondary"
              disabled={conflictLoading}
              onClick={() => void loadLatest()}
            >
              {conflictLoading ? "Загружаем…" : "Показать актуальную версию"}
            </button>
          )}
        {latestError != null && (
          <ErrorNotice error={latestError} retry={() => void loadLatest()} />
        )}
        {latest && (
          <section className="notice conflict">
            <div>
              <h2>Актуальная версия {latest.current_version.version_number}</h2>
              <p>
                Ваш черновик остаётся ниже. Сравните его с сохранёнными
                правилами перед повторной отправкой.
              </p>
              <details>
                <summary>Посмотреть сохранённые критерии</summary>
                {latest.current_version.criteria.map((c) => (
                  <div key={c.id} className="comparison">
                    <strong>{c.name}</strong>
                    <p>{c.instruction}</p>
                    <p>
                      Тип: {c.type} · вес: {c.weight} · N/A:{" "}
                      {c.allow_na ? c.na_rule : "нет"} · секция:{" "}
                      {c.section ?? "нет"}
                    </p>
                    {c.scale?.map((s) => (
                      <p key={s.value}>
                        {s.value} — {s.description}
                      </p>
                    ))}
                  </div>
                ))}
              </details>
              <div className="inline">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setExpected(latest.current_version.version_number);
                    setHistory(latest.versions);
                    setLatest(undefined);
                    setError(undefined);
                    setDirty(true);
                  }}
                >
                  Сохранить черновик для повторной отправки
                </button>
                <button
                  type="button"
                  className="text-button"
                  onClick={() => setConfirmReplace(true)}
                >
                  Заменить черновик актуальной версией
                </button>
              </div>
            </div>
          </section>
        )}
        {success && (
          <div className="notice success" role="status">
            {success}
          </div>
        )}
        <fieldset disabled={busy} className="editor-fieldset">
          <div className="panel editor-meta">
            <label>
              Название чек-листа
              <input
                value={name}
                readOnly={!!initial}
                onChange={(e) => {
                  setName(e.target.value);
                  setDirty(true);
                }}
              />
            </label>
            {initial && (
              <label>
                История версий
                <select
                  value={viewedVersion}
                  disabled={dirty}
                  onChange={(e) => {
                    const v = history.find((v) => v.id === e.target.value);
                    if (v) replaceWith(v);
                  }}
                >
                  {history.map((v) => (
                    <option key={v.id} value={v.id}>
                      Версия {v.version_number}
                    </option>
                  ))}
                </select>
                <small>
                  Старую версию можно взять за основу новой. Текущий черновик
                  сначала сохраните.
                </small>
              </label>
            )}
          </div>
          <div className="editor-heading">
            <h2>
              Критерии <span className="count">{criteria.length}</span>
            </h2>
            <span className="muted">Вес шкалы × ответ / максимум</span>
          </div>
          {criteria.map((c, index) => (
            <section
              className="panel criterion"
              key={c.id}
              aria-label={`Критерий ${index + 1}`}
            >
              <div className="criterion-top">
                <span className="section-number">
                  КРИТЕРИЙ {String(index + 1).padStart(2, "0")}
                </span>
                <button
                  type="button"
                  className="text-button danger"
                  onClick={() => {
                    setCriteria((cs) => cs.filter((_, i) => i !== index));
                    setDirty(true);
                  }}
                >
                  Удалить критерий {index + 1}
                </button>
              </div>
              <div className="criterion-fields">
                <label className="wide">
                  Название критерия
                  <input
                    value={c.name}
                    onChange={(e) => edit(index, { name: e.target.value })}
                  />
                </label>
                <label>
                  Тип ответа
                  <select
                    value={c.type}
                    onChange={(e) =>
                      changeType(index, e.target.value as Criterion["type"])
                    }
                  >
                    <option value="binary">Да / Нет</option>
                    <option value="scale">Числовая шкала</option>
                    <option value="flag">Флаг нарушения</option>
                  </select>
                </label>
                <label>
                  Вес
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={c.weight}
                    disabled={c.type === "flag"}
                    onChange={(e) => edit(index, { weight: e.target.value })}
                  />
                </label>
                <label className="full">
                  Инструкция для оценки
                  <textarea
                    rows={3}
                    value={c.instruction}
                    onChange={(e) =>
                      edit(index, { instruction: e.target.value })
                    }
                  />
                </label>
                <label className="full">
                  Секция (необязательно)
                  <input
                    value={c.section ?? ""}
                    onChange={(e) => edit(index, { section: e.target.value })}
                  />
                </label>
              </div>
              {c.type === "flag" && (
                <p className="hint">
                  «Да» означает обнаруженное нарушение. Флаг не участвует в
                  расчёте баллов.
                </p>
              )}
              {c.type === "scale" && (
                <div className="scale-editor">
                  <h3>Ступени шкалы</h3>
                  <p className="muted">
                    Минимум две разные неотрицательные ступени. Максимум должен
                    быть больше нуля.
                  </p>
                  {c.scale.map((step, si) => (
                    <div className="scale-row" key={si}>
                      <label>
                        Значение ступени {si + 1}
                        <input
                          type="number"
                          min="0"
                          step="any"
                          value={step.value}
                          onChange={(e) =>
                            edit(index, {
                              scale: c.scale.map((s, i) =>
                                i === si ? { ...s, value: e.target.value } : s,
                              ),
                            })
                          }
                        />
                      </label>
                      <label>
                        Описание ступени {si + 1}
                        <input
                          value={step.description}
                          onChange={(e) =>
                            edit(index, {
                              scale: c.scale.map((s, i) =>
                                i === si
                                  ? { ...s, description: e.target.value }
                                  : s,
                              ),
                            })
                          }
                        />
                      </label>
                      <button
                        type="button"
                        className="text-button danger"
                        onClick={() =>
                          edit(index, {
                            scale: c.scale.filter((_, i) => i !== si),
                          })
                        }
                      >
                        Удалить ступень {si + 1}
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="secondary"
                    onClick={() =>
                      edit(index, {
                        scale: [...c.scale, { value: "", description: "" }],
                      })
                    }
                  >
                    + Добавить ступень
                  </button>
                </div>
              )}
              <div className="na-editor">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={c.allow_na}
                    disabled={c.type === "flag"}
                    onChange={(e) =>
                      edit(index, {
                        allow_na: e.target.checked,
                        na_rule: e.target.checked ? c.na_rule : "",
                      })
                    }
                  />
                  Разрешить N/A — критерий неприменим
                </label>
                {c.allow_na && (
                  <label>
                    Условие применения N/A
                    <textarea
                      rows={2}
                      value={c.na_rule}
                      onChange={(e) => edit(index, { na_rule: e.target.value })}
                    />
                  </label>
                )}
              </div>
            </section>
          ))}
          <button
            type="button"
            className="secondary add-criterion"
            onClick={() => {
              setCriteria((cs) => [...cs, newCriterion()]);
              setDirty(true);
            }}
          >
            + Добавить критерий
          </button>
        </fieldset>
        <div className="save-bar">
          <div>
            <strong>
              {dirty
                ? "Есть несохранённые изменения"
                : "Все изменения сохранены"}
            </strong>
            <small>Предыдущие версии останутся доступны.</small>
          </div>
          <button disabled={busy || !!latest || (initial != null && !dirty)}>
            {busy
              ? "Сохраняем…"
              : initial
                ? "Сохранить новую версию"
                : "Создать чек-лист"}
          </button>
        </div>
      </form>
      {(blocker.state === "blocked" || confirmReplace) && (
        <ConfirmDialog
          onCancel={() => {
            if (confirmReplace) setConfirmReplace(false);
            else if (blocker.state === "blocked") blocker.reset();
          }}
        >
          <h2 id="leave-title">Несохранённые изменения</h2>
          <p>
            {confirmReplace
              ? "Заменить ваш черновик актуальными правилами? Введённые изменения будут потеряны."
              : "Покинуть редактор? Несохранённые изменения будут потеряны."}
          </p>
          <div className="inline">
            <button
              autoFocus
              onClick={() => {
                if (confirmReplace) setConfirmReplace(false);
                else if (blocker.state === "blocked") blocker.reset();
              }}
            >
              Остаться
            </button>
            <button
              className="secondary"
              onClick={() => {
                if (confirmReplace && latest) {
                  setExpected(latest.current_version.version_number);
                  setHistory(latest.versions);
                  replaceWith(latest.current_version);
                  setDirty(false);
                  setLatest(undefined);
                  setError(undefined);
                  setConfirmReplace(false);
                } else if (blocker.state === "blocked") blocker.proceed();
              }}
            >
              {confirmReplace ? "Заменить черновик" : "Покинуть редактор"}
            </button>
          </div>
        </ConfirmDialog>
      )}
    </>
  );
}
