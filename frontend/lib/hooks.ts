"use client";

import { useCallback, useEffect, useState } from "react";

interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
}

interface ApiOptions {
  /**
   * Keep the last successful data while a refetch loads or after it fails
   * (background refreshes, filter changes) instead of resetting to null.
   * `error` still reports a failed refetch.
   */
  keepPrevious?: boolean;
}

export function useApi<T>(
  fn: () => Promise<T>,
  deps: unknown[] = [],
  { keepPrevious = false }: ApiOptions = {},
): AsyncState<T> {
  const [tick, setTick] = useState(0);
  const depKey = deps.map((d) => String(d)).join("|") + "#" + tick;
  const [state, setState] = useState<{ key: string; data: T | null; error: string | null }>({
    key: "",
    data: null,
    error: null,
  });

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    const key = depKey;
    fn()
      .then((d) => {
        if (!cancelled) setState({ key, data: d, error: null });
      })
      .catch((e: unknown) => {
        if (!cancelled)
          setState((prev) => ({
            key,
            data: keepPrevious ? prev.data : null,
            error: e instanceof Error ? e.message : String(e),
          }));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depKey]);

  const current = state.key === depKey;
  return {
    data: current || keepPrevious ? state.data : null,
    error: current ? state.error : null,
    loading: !current,
    refetch,
  };
}
