import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { ApiClient } from "./api/client";
import { ApiContext } from "./api/context";
import { createMockTransport } from "./mock/transport";
import { ErrorBoundary } from "./components/common";

function initialMode(): "mock" | "api" {
  const requested = new URLSearchParams(window.location.search).get("mode");
  if (requested === "mock" || requested === "api") {
    sessionStorage.setItem("cqa-mode", requested);
    return requested;
  }
  const stored = sessionStorage.getItem("cqa-mode");
  if (stored === "api" || stored === "mock") return stored;
  return import.meta.env.VITE_DATA_MODE === "mock" ? "mock" : "api";
}
export function App() {
  const [mode] = useState(initialMode);
  const [api] = useState(
    () => new ApiClient(mode === "mock" ? createMockTransport() : undefined),
  );
  return (
    <ApiContext.Provider value={api}>
      <div className="app-shell">
        <a className="skip-link" href="#main">
          Перейти к содержимому
        </a>
        <aside className="sidebar">
          <a href="/" className="brand">
            <span className="brand-mark">H</span>
            <span>
              HULK’S FAMILY<small>CONVERSATION QUALITY</small>
            </span>
          </a>
          <span className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</span>
          <nav aria-label="Основная навигация">
            <NavLink to="/conversations">
              <span aria-hidden>▤</span>Диалоги
            </NavLink>
            <NavLink to="/checklists">
              <span aria-hidden>☷</span>Чек-листы
            </NavLink>
          </nav>
          <div className="sidebar-footer">
            <span className="status-dot" />
            CQA / Freedom Broker<small>Качество начинается с внимания.</small>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <span>Аналитика качества общения</span>
            <label className="mode-select">
              Источник данных
              <select
                aria-label="Источник данных"
                value={mode}
                onChange={(e) => {
                  window.location.href = `/?mode=${e.target.value}`;
                }}
              >
                <option value="api">Настоящий API</option>
                <option value="mock">Синтетические данные</option>
              </select>
            </label>
          </header>
          {mode === "mock" && (
            <div className="mock-banner" role="status">
              <strong>ДЕМО · СИНТЕТИЧЕСКИЕ ДАННЫЕ</strong>
              <span>
                Изменения действуют до перезагрузки. AI-анализ не выполняется.
              </span>
            </div>
          )}
          <main id="main" tabIndex={-1}>
            <ErrorBoundary>
              <Outlet />
            </ErrorBoundary>
          </main>
          <footer className="workspace-footer">
            <span>HULK’S FAMILY / CQA</span>
            <span>Контекст. Правила. Прозрачность.</span>
          </footer>
        </div>
      </div>
    </ApiContext.Provider>
  );
}
