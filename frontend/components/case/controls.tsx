"use client";

import { useCallback, useEffect, useState } from "react";
import { Pause, Play, RotateCcw, Square, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { CaseControlView, TrashPreview } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB"];
  let v = bytes;
  let u = -1;
  do { v /= 1024; u += 1; } while (v >= 1024 && u < units.length - 1);
  return `${v.toFixed(1)} ${units[u]}`;
}

const STATE_LABEL: Record<string, string> = {
  running: "Running",
  pausing: "Pausing… waiting for current step",
  paused: "Paused",
  stopped: "Stopped",
  trashed: "In trash",
};

function stateBadge(state: string) {
  if (state === "running") return null;
  const variant =
    state === "pausing" ? "warning" : state === "trashed" ? "danger" : "outline";
  return <Badge variant={variant}>{STATE_LABEL[state] ?? state}</Badge>;
}

/** Pause / Resume / Stop(+Restore) for one case — the same three buttons wherever a case
 * is shown. `compact` renders the small row version for lists. */
export function CaseControls({
  caseId,
  caseTitle,
  compact = false,
  onChanged,
}: {
  caseId: number;
  caseTitle: string;
  compact?: boolean;
  onChanged?: () => void;
}) {
  const [view, setView] = useState<CaseControlView | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stopOpen, setStopOpen] = useState(false);
  const [pauseOpen, setPauseOpen] = useState(false);
  const [pauseLangs, setPauseLangs] = useState<Set<string>>(new Set());

  const refresh = useCallback(() => {
    api.caseControl(caseId).then(setView).catch(() => setView(null));
  }, [caseId]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, view?.state === "pausing" ? 1500 : 8000);
    return () => clearInterval(t);
  }, [refresh, view?.state]);

  const act = async (kind: string, fn: () => Promise<unknown>) => {
    setBusy(kind);
    setError(null);
    try {
      await fn();
      refresh();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  if (!view) return null;
  const state = view.state;
  const paused = state === "paused" || state === "pausing";
  const stopped = state === "stopped";
  const trashed = state === "trashed";
  const running = state === "running";

  return (
    <div className={compact ? "flex items-center gap-1" : "flex flex-wrap items-center gap-2"}
      data-testid={`case-controls-${caseId}`}>
      {!compact && state !== "running" && stateBadge(state)}
      {view.paused_languages.length > 0 && !compact && (
        <Badge variant="outline">paused: {view.paused_languages.join(", ")}</Badge>
      )}

      <Button
        variant={compact ? "ghost" : "secondary"}
        size={compact ? "sm" : "sm"}
        disabled={!running || busy !== null}
        loading={busy === "pause"}
        title="Pause at the next step boundary — in-flight calls finish, nothing new starts"
        onClick={() => setPauseOpen(true)}
      >
        <Pause className="size-3.5" /> {!compact && "Pause"}
      </Button>
      <Button
        variant={compact ? "ghost" : "secondary"}
        size="sm"
        disabled={!(paused || stopped || view.paused_languages.length > 0) || trashed || busy !== null}
        loading={busy === "resume"}
        title="Resume exactly where it stopped — completed steps are never repeated"
        onClick={() => act("resume", () => api.resumeCase(caseId))}
      >
        <Play className="size-3.5" /> {!compact && "Resume"}
      </Button>
      {trashed ? (
        <Button
          variant="secondary"
          size="sm"
          disabled={!view.restorable || busy !== null}
          loading={busy === "restore"}
          title="Bring the case back from the trash"
          onClick={() => act("restore", () => api.restoreCase(caseId))}
        >
          <RotateCcw className="size-3.5" /> {!compact && "Restore"}
        </Button>
      ) : (
        <Button
          variant={compact ? "ghost" : "outline"}
          size="sm"
          disabled={busy !== null}
          title="Stop this case — keep everything or delete it"
          onClick={() => setStopOpen(true)}
        >
          <Square className="size-3.5" /> {!compact && "Stop"}
        </Button>
      )}

      {error && !compact && <span className="text-xs text-rose-600">{error}</span>}
      {stopOpen && (
        <StopDialog
          caseId={caseId}
          caseTitle={caseTitle}
          open
          busy={busy}
          onClose={() => setStopOpen(false)}
          onAct={(kind, fn) => act(kind, fn)}
        />
      )}
      {pauseOpen && (
        <Dialog open onClose={() => setPauseOpen(false)} title="Pause this case">
          <div className="space-y-4" data-testid="pause-dialog">
            <p className="text-sm text-muted-foreground">
              Stops at the next step boundary — in-flight calls finish, nothing new
              starts. The queue is held, not deleted.
            </p>
            <fieldset className="space-y-2">
              <legend className="text-xs font-medium text-muted-foreground">Scope</legend>
              {(view.languages ?? []).map((lang) => (
                <label key={lang} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={pauseLangs.has(lang)}
                    onChange={(e) => {
                      const next = new Set(pauseLangs);
                      if (e.target.checked) next.add(lang); else next.delete(lang);
                      setPauseLangs(next);
                    }}
                    data-testid={`pause-lang-${lang}`}
                  />
                  {lang}
                </label>
              ))}
              <p className="text-[11px] text-muted-foreground">
                No languages checked = pause the whole case. Checked = hold only those
                languages at their next boundary.
              </p>
            </fieldset>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setPauseOpen(false)}>Cancel</Button>
              <Button
                loading={busy === "pause"}
                data-testid="pause-confirm"
                onClick={() =>
                  act("pause", async () => {
                    await api.pauseCase(caseId, {
                      languages: pauseLangs.size ? [...pauseLangs] : undefined,
                    });
                    setPauseOpen(false);
                  })
                }
              >
                Pause
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function StopDialog({
  caseId,
  caseTitle,
  open,
  busy,
  onClose,
  onAct,
}: {
  caseId: number;
  caseTitle: string;
  open: boolean;
  busy: string | null;
  onClose: () => void;
  onAct: (kind: string, fn: () => Promise<unknown>) => void;
}) {
  const [mode, setMode] = useState<"keep" | "delete">("keep");
  const [typed, setTyped] = useState("");
  const [preview, setPreview] = useState<TrashPreview | null>(null);

  useEffect(() => {
    if (!open) return;
    api.caseTrashPreview(caseId).then(setPreview).catch(() => setPreview(null));
  }, [open, caseId]);

  const nameOk = typed.trim() === caseTitle;

  return (
    <Dialog open={open} onClose={onClose} title="Stop this case">
      <div className="space-y-4" data-testid="stop-dialog">
        <div className="space-y-2">
          <label className="flex cursor-pointer items-start gap-2 rounded-md border border-border p-3">
            <input
              type="radio"
              name="stop-mode"
              className="mt-0.5"
              checked={mode === "keep"}
              onChange={() => setMode("keep")}
              data-testid="stop-keep"
            />
            <span>
              <span className="block text-sm font-medium">Stop and keep</span>
              <span className="block text-xs text-muted-foreground">
                Halt everything. All data stays; the case can be resumed later.
              </span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-2 rounded-md border border-rose-500/40 p-3">
            <input
              type="radio"
              name="stop-mode"
              className="mt-0.5"
              checked={mode === "delete"}
              onChange={() => setMode("delete")}
              data-testid="stop-delete"
            />
            <span>
              <span className="block text-sm font-medium text-rose-600 dark:text-rose-400">
                <Trash2 className="mr-1 inline size-3.5" />
                Stop and delete everything about this case
              </span>
              <span className="block text-xs text-muted-foreground">
                24 hours in the trash, then removed for good. Cost ledger, tombstone and
                audit log are kept forever.
              </span>
            </span>
          </label>
        </div>

        {mode === "delete" && (
          <div className="space-y-3">
            <div className="rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
              {preview ? (
                <>
                  This will delete <b>{preview.files.toLocaleString()}</b> files (
                  {formatBytes(preview.bytes)}),{" "}
                  <b>{preview.db_rows.toLocaleString()}</b> database rows. Spent so far:{" "}
                  <b>€{preview.cost_eur.toFixed(2)}</b> — the ledger stays.
                </>
              ) : (
                "Counting what would be deleted…"
              )}
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="stop-confirm">
                Are you sure you want to delete all things about this case? Type the case
                name to confirm:
              </label>
              <Input
                id="stop-confirm"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                placeholder={caseTitle}
                data-testid="stop-confirm-name"
              />
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            variant={mode === "delete" ? "destructive" : "default"}
            disabled={mode === "delete" && (!nameOk || preview === null) || busy !== null}
            loading={busy === "stop"}
            data-testid="stop-confirm"
            onClick={() =>
              onAct("stop", async () => {
                await api.stopCase(caseId, {
                  mode,
                  confirm_name: mode === "delete" ? typed.trim() : undefined,
                });
                onClose();
              })
            }
          >
            {mode === "delete" ? "Delete this case" : "Stop and keep"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
