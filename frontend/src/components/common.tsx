import { Component, useEffect, useRef, type ReactNode } from "react";
import { errorText, logEvent } from "../api/errors";

export function Loading() {
  return (
    <div className="state" role="status">
      <span className="spinner" />
      Загружаем данные…
    </div>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div className="notice error" role="alert">
      <div>
        <strong>Не удалось выполнить запрос</strong>
        <p>{errorText(error)}</p>
      </div>
      {retry && (
        <button className="secondary" onClick={retry}>
          Повторить запрос
        </button>
      )}
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="state empty">
      <span className="empty-icon" aria-hidden>
        ◌
      </span>
      <h2>{title}</h2>
      {children}
    </div>
  );
}
export function PageTitle({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-title">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
      </div>
      {children}
    </header>
  );
}
export function ChannelTag({
  channel,
}: {
  channel: "agent chat" | "voice call";
}) {
  return (
    <span className={`tag ${channel === "voice call" ? "voice" : ""}`}>
      {channel === "voice call" ? "Звонок" : "▤ Чат"}
    </span>
  );
}
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    logEvent("ui_failed", 0, "UI_ERROR");
  }
  render() {
    return this.state.failed ? <SafeErrorScreen /> : this.props.children;
  }
}

export function ConfirmDialog({
  children,
  onCancel,
}: {
  children: ReactNode;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal panel"
      aria-labelledby="leave-title"
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
    >
      {children}
    </dialog>
  );
}
export function SafeErrorScreen() {
  return (
    <div className="state" role="alert">
      <h1>Не удалось показать экран</h1>
      <button onClick={() => window.location.reload()}>
        Перезагрузить страницу
      </button>
    </div>
  );
}

export function OpenArrow() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      <path d="M6 18 18 6M6 6h12v12" />
    </svg>
  );
}
