import { useEffect, useRef, useState } from "react";
import { useApi } from "../api/context";
import { ApiError, logEvent, safeCode } from "../api/errors";
import type { Evaluation } from "../api/schema";

export const isPending = (status: Evaluation["status"]) =>
  status === "queued" || status === "running";

export function useEvaluation(conversationId: string, id: string) {
  const api = useApi();
  const [state, setState] = useState<{
    id: string;
    data?: Evaluation;
    error?: unknown;
  }>();
  const [retryCount, setRetryCount] = useState(0);
  const reportedFailure = useRef(new Set<string>());

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setState((prev) => ({ id, data: prev?.id === id ? prev.data : undefined }));
    async function read() {
      try {
        const result = await api.evaluation(id, controller.signal);
        if (controller.signal.aborted) return;
        if (result.id !== id || result.conversation_id !== conversationId) {
          logEvent("load_failed", 0, "INVALID_RESPONSE");
          throw new ApiError("INVALID_RESPONSE");
        }
        setState((prev) =>
          prev?.id === id && (prev.data?.revision ?? 0) > result.revision
            ? prev
            : { id, data: result },
        );
        if (isPending(result.status)) timer = setTimeout(read, 1_000);
        if (result.status === "failed" && !reportedFailure.current.has(id)) {
          reportedFailure.current.add(id);
          logEvent(
            "evaluation_run_failed",
            200,
            safeCode(result.error.code, 500),
          );
        }
      } catch (error) {
        if (!controller.signal.aborted)
          setState((prev) => ({
            id,
            data: prev?.id === id ? prev.data : undefined,
            error,
          }));
      }
    }
    void read();
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [api, conversationId, id, retryCount]);

  const current = state?.id === id ? state : undefined;
  return {
    data: current?.data,
    error: current?.error,
    retry: () => setRetryCount((n) => n + 1),
    update(result: Evaluation) {
      if (result.id !== id || result.conversation_id !== conversationId) return;
      setState((prev) =>
        prev?.id === id && (prev.data?.revision ?? 0) > result.revision
          ? prev
          : { id, data: result },
      );
    },
  };
}
