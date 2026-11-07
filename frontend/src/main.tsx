import React from "react";
import ReactDOM from "react-dom/client";
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
} from "react-router-dom";
import { App } from "./App";
import { Conversations } from "./features/Conversations";
import { ConversationDetail } from "./features/ConversationDetail";
import { Checklists } from "./features/Checklists";
import { ChecklistEditor } from "./features/ChecklistEditor";
import { SafeErrorScreen } from "./components/common";
import { logEvent } from "./api/errors";
import "./styles.css";
const router = createBrowserRouter([
  {
    element: <App />,
    errorElement: <SafeErrorScreen />,
    children: [
      {
        path: "/",
        element: (
          <Navigate to={`/conversations${window.location.search}`} replace />
        ),
      },
      { path: "/conversations", element: <Conversations /> },
      { path: "/conversations/:id", element: <ConversationDetail /> },
      { path: "/checklists", element: <Checklists /> },
      { path: "/checklists/new", element: <ChecklistEditor /> },
      { path: "/checklists/:id", element: <ChecklistEditor /> },
      {
        path: "*",
        element: (
          <div className="state">
            <h1>Страница не найдена</h1>
            <a href="/">Вернуться к диалогам</a>
          </div>
        ),
      },
    ],
  },
]);
ReactDOM.createRoot(document.getElementById("root")!, {
  onCaughtError: () => {
    /* Owning boundary/router logs one safe event. */
  },
  onUncaughtError: () => logEvent("ui_failed", 0, "UI_ERROR"),
  onRecoverableError: () => logEvent("ui_failed", 0, "UI_ERROR"),
}).render(
  <React.StrictMode>
    <RouterProvider
      router={router}
      onError={() => logEvent("ui_failed", 0, "UI_ERROR")}
    />
  </React.StrictMode>,
);
