"use client";

import { useState } from "react";
import { HardDriveDownload } from "lucide-react";
import { backupApi, ApiError, type BackupSettings } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

function mb(bytes: number | null): string {
  return bytes == null ? "" : `${(bytes / 1_000_000).toFixed(1)} MB`;
}

const FIELD = "rounded-md border border-border bg-background px-2 py-1 text-sm";

export function BackupCard() {
  const { data, error, refetch } = useApi(() => backupApi.overview(), [], { keepPrevious: true });
  // What the user has typed on top of the saved settings; later refreshes never overwrite it.
  const [edits, setEdits] = useState<Partial<BackupSettings>>({});
  const [busy, setBusy] = useState<"run" | "save" | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  if (error && !data) return null; // the rest of the dashboard already shows API errors
  if (!data) return null;
  const form: BackupSettings = { ...data.settings, ...edits };
  const scriptBackups = data.folder_backups ?? [];

  const set = <K extends keyof BackupSettings>(k: K, v: BackupSettings[K]) => setEdits({ ...edits, [k]: v });

  async function save() {
    setBusy("save");
    setMessage(null);
    try {
      await backupApi.saveSettings(form);
      setMessage({ ok: true, text: "Saved." });
      refetch();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : "Could not save." });
    } finally {
      setBusy(null);
    }
  }

  async function runNow() {
    setBusy("run");
    setMessage(null);
    try {
      const run = await backupApi.runNow(form.dir);
      setMessage(
        run.status === "ok"
          ? { ok: true, text: `Backup written (${mb(run.size_bytes)}): ${run.file_path}` }
          : { ok: false, text: run.error ?? "Backup failed." },
      );
      refetch();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : "Backup failed." });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardContent className="space-y-4 py-4">
        <div className="flex items-center gap-2">
          <HardDriveDownload className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">Database backup</h2>
          {scriptBackups[0] ? (
            <span className="text-xs text-muted-foreground">
              latest script backup: {formatDateTime(scriptBackups[0].created_at)}
            </span>
          ) : data.last_ok_at ? (
            <span className="text-xs text-muted-foreground">last successful: {formatDateTime(data.last_ok_at)}</span>
          ) : (
            <Badge variant="warning">no backup yet</Badge>
          )}
        </div>

        <div className="flex flex-wrap items-end gap-2">
          <label className="flex min-w-64 flex-1 flex-col gap-1 text-xs text-muted-foreground">
            Backup folder (e.g. /Volumes/MyDrive/truecrime-backups)
            <input
              className={FIELD}
              value={form.dir}
              onChange={(e) => set("dir", e.target.value)}
              placeholder="/Volumes/…"
              spellCheck={false}
            />
          </label>
          <Button onClick={runNow} disabled={busy !== null || !form.dir.trim()}>
            {busy === "run" ? "Backing up…" : "Back up now"}
          </Button>
        </div>

        {scriptBackups.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
              Backups created by <code>scripts/backup_db.sh</code> ({data.script_backup_dir})
            </p>
            <ul className="divide-y divide-border text-xs" aria-label="Script database backups">
              {scriptBackups.slice(0, 5).map((backup) => (
                <li key={backup.file_path} className="flex flex-wrap items-center gap-2 py-1.5">
                  <Badge variant="success">on disk</Badge>
                  <span className="text-muted-foreground">{formatDateTime(backup.created_at)}</span>
                  <span className="min-w-0 flex-1 truncate" title={backup.file_path}>{backup.file_name}</span>
                  <span className="text-muted-foreground">{mb(backup.size_bytes)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex flex-wrap items-end gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
            Automatic backups
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Every (hours)
            <input
              type="number"
              min={1}
              max={720}
              className={`${FIELD} w-20`}
              value={form.every_hours}
              onChange={(e) => set("every_hours", Number(e.target.value))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Starting at
            <input
              type="time"
              className={FIELD}
              value={form.start_time}
              onChange={(e) => set("start_time", e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Keep last (0 = all)
            <input
              type="number"
              min={0}
              max={1000}
              className={`${FIELD} w-20`}
              value={form.keep_last}
              onChange={(e) => set("keep_last", Number(e.target.value))}
            />
          </label>
          <Button variant="outline" onClick={save} disabled={busy !== null}>
            {busy === "save" ? "Saving…" : "Save schedule"}
          </Button>
        </div>

        {data.settings.enabled && data.next_run_at && (
          <p className="text-xs text-muted-foreground">Next automatic backup: {formatDateTime(data.next_run_at)}</p>
        )}
        {message && (
          <p role="status" className={`text-sm ${message.ok ? "text-emerald-600 dark:text-emerald-400" : "text-rose-500"}`}>
            {message.text}
          </p>
        )}

        {data.runs.length > 0 && (
          <ul className="divide-y divide-border text-xs" aria-label="Recent backups">
            {data.runs.slice(0, 5).map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2 py-1.5">
                <Badge variant={r.status === "ok" ? "success" : r.status === "failed" ? "danger" : "info"}>
                  {r.status}
                </Badge>
                <span className="text-muted-foreground">{formatDateTime(r.started_at)}</span>
                <span className="text-muted-foreground">{r.trigger}</span>
                <span className="min-w-0 flex-1 truncate">{r.status === "ok" ? r.file_path : r.error}</span>
                {r.size_bytes != null && <span className="text-muted-foreground">{mb(r.size_bytes)}</span>}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
