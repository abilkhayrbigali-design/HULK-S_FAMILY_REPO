import { useEffect, useRef, useState } from "react";

// A ref closes the gap before React renders the disabled button.
export function useMutation() {
  const active = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  useEffect(() => () => active.current?.abort(), []);

  async function submit<T>(request: (signal: AbortSignal) => Promise<T>) {
    if (active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError(undefined);
    try {
      const data = await request(controller.signal);
      if (!controller.signal.aborted) return { ok: true as const, data };
    } catch (error) {
      if (!controller.signal.aborted) {
        setError(error);
        return { ok: false as const, error };
      }
    } finally {
      active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return { submit, busy, error, clearError: () => setError(undefined) };
}
