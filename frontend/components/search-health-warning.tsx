"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/config";

export function SearchHealthWarning() {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const check = () =>
      fetch(`${API_BASE}/health`, { credentials: "include" })
        .then(async (response) => {
          const body = await response.json().catch(() => ({}));
          if (!cancelled) setFailed(!response.ok || body.search === "failing");
        })
        .catch(() => !cancelled && setFailed(true));
    check();
    const timer = window.setInterval(check, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  if (!failed) return null;
  return (
    <div
      role="alert"
      className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-200"
    >
      Search is unavailable or returned no results. Discovery and research may complete with no candidates until SearXNG is healthy.
    </div>
  );
}
