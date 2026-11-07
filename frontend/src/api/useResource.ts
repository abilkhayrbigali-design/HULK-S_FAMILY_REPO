import { useEffect, useState } from "react";

// Callers memoize load; stale/cancelled requests cannot replace a newer screen.
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<{
    data?: T;
    error?: unknown;
    loading: boolean;
  }>({ loading: true });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ loading: true });
    load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, loading: false });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setState({ error, loading: false });
      });
    return () => controller.abort();
  }, [load, revision]);
  return { ...state, retry: () => setRevision((v) => v + 1) };
}
