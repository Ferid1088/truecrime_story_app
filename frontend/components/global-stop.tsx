"use client";

import { useCallback, useEffect, useState } from "react";
import { OctagonAlert, Play } from "lucide-react";
import { api } from "@/lib/api";
import type { GlobalStopState } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";

/** The emergency stop in the app sidebar: one click halts autopilot, discovery and every
 * queue that can spend money. Persisted server-side — survives restarts. */
export function EmergencyStop() {
  const [state, setState] = useState<GlobalStopState | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(
    () => api.globalStop().then(setState).catch(() => setState(null)),
    [],
  );
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  }, [refresh]);

  const set = async (active: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await api.setGlobalStop(active);
      setConfirm(false);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const active = state?.active ?? false;
  return (
    <>
      {active ? (
        <button
          type="button"
          onClick={() => set(false)}
          disabled={busy}
          className="flex w-full items-center justify-between rounded-md border border-rose-500/50 bg-rose-500/10 px-2 py-1.5 text-left text-xs font-semibold text-rose-700 dark:text-rose-300"
          title={`Stopped ${state?.at ?? ""} by ${state?.by ?? "?"} — click to release`}
          data-testid="global-stop-active"
        >
          <span className="flex items-center gap-1.5">
            <OctagonAlert className="size-3.5" /> GLOBAL STOP — all queues held
          </span>
          <Play className="size-3" />
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setConfirm(true)}
          className="flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-xs text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
          data-testid="global-stop-button"
        >
          <OctagonAlert className="size-3.5" /> Emergency stop
        </button>
      )}
      {error && <p className="px-2 text-[11px] text-rose-600">{error}</p>}

      <Dialog open={confirm} onClose={() => setConfirm(false)} title="Emergency stop">
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            This stops the autopilot and every queue that can spend money, for every case.
            In-flight provider calls finish; nothing new is claimed. It stays active until
            you release it — even across restarts.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirm(false)}>Cancel</Button>
            <Button variant="destructive" loading={busy} onClick={() => set(true)}
              data-testid="global-stop-confirm">
              Stop everything
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}
