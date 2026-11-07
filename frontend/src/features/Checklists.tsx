import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api/context";
import { useResource } from "../api/useResource";
import { Empty, ErrorNotice, Loading, PageTitle } from "../components/common";

export function Checklists() {
  const api = useApi();
  const [offset, setOffset] = useState(0);
  const list = useResource(
    useCallback((signal) => api.checklists(offset, signal), [api, offset]),
  );
  return (
    <>
      <PageTitle eyebrow="Рабочее пространство / Правила" title="Чек-листы">
        <Link className="button" to="/checklists/new">
          + Создать чек-лист
        </Link>
      </PageTitle>
      <p className="intro">
        Правила, по которым оценивается работа оператора. Каждое сохранение
        создаёт новую версию — история остаётся неизменной.
      </p>
      {list.loading ? (
        <Loading />
      ) : list.error ? (
        <ErrorNotice error={list.error} retry={list.retry} />
      ) : !list.data?.items.length ? (
        <Empty title="Создайте первый чек-лист">
          <p>Добавьте свои критерии и правила применения.</p>
        </Empty>
      ) : (
        <>
          <div className="checklist-grid">
            {list.data.items.map((c) => (
              <Link
                className="panel checklist-card"
                to={`/checklists/${c.id}`}
                key={c.id}
              >
                <span className="section-number">ПРАВИЛА ОЦЕНКИ</span>
                <h2>{c.name}</h2>
                <div>
                  <span className="tag">Версия {c.latest_version_number}</span>
                  <span aria-hidden>Открыть редактор ↗</span>
                </div>
              </Link>
            ))}
          </div>
          <div className="pagination">
            <span>{list.data.total} чек-листов</span>
            <div>
              <button
                className="secondary"
                disabled={!offset}
                onClick={() => setOffset((o) => Math.max(0, o - 100))}
              >
                Назад
              </button>
              <button
                className="secondary"
                disabled={offset + 100 >= list.data.total}
                onClick={() => setOffset((o) => o + 100)}
              >
                Далее
              </button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
