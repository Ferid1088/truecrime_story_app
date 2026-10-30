"use client";

import { useEffect, useState } from "react";
import { ApiError, apiFileUrl, drawingApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button } from "@/components/ui/button";

const STATUS_TEXT = { none: "not made", drawing: "being drawn", ready: "ready", failed: "failed" } as const;
const REFRESH_MS = 5000;

/** The pencil portraits: the planner says how many the film needs and which of the chosen photos they are; one button makes them. */
export function PortraitsPanel({ caseId }: { caseId: number }) {
  const { data, refetch } = useApi(() => drawingApi.overview(caseId), [caseId]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const drawing = data?.drawing ?? 0;

  useEffect(() => {
    if (!drawing) return;
    const timer = setInterval(() => void refetch(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [drawing, refetch]);

  if (!data) return null;
  const allReady = data.needed > 0 && data.ready >= data.needed;

  async function create() {
    setBusy(true);
    setMessage("");
    try {
      await drawingApi.create(caseId);
      await refetch();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "The portraits could not be started.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mb-3 rounded-md border border-violet-500/40 bg-violet-500/5 p-3 text-sm" data-testid="portraits-panel">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-medium">Pencil portraits</h2>
        <span data-testid="portraits-summary" className="text-muted-foreground">
          {!data.planned
            ? "The planner has not worked out how many portraits the film needs yet: run the planning job."
            : data.chosen === 0
              ? "No photo is chosen: give approved photos of people a Drawing priority."
              : `The film needs ${data.needed} portrait${data.needed === 1 ? "" : "s"}${data.speed ? ` (drawing speed ${data.speed}×)` : ""}: ${data.ready} of ${data.needed} made.`}
        </span>
        <Button
          size="sm"
          className="ml-auto"
          onClick={() => void create()}
          disabled={busy || !data.planned || data.chosen === 0 || data.uncovered.length > 0 || allReady || drawing > 0}
          data-testid="create-portraits"
        >
          {drawing > 0 ? "Drawing…" : allReady ? "Portraits made" : "Create portraits"}
        </Button>
      </div>
      {data.uncovered.length > 0 && (
        <p className="mt-2 text-rose-700" role="alert">
          A gap of the film has no portrait that may be shown without a spoiler: give a photo of someone named earlier the Drawing priority 1.
        </p>
      )}
      {message && <p className="mt-2 text-rose-700" role="alert">{message}</p>}
      {data.portraits.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-2" data-testid="portraits-list">
          {data.portraits.map((p) => (
            <li key={p.asset_id} className={`flex items-center gap-2 rounded border p-1.5 text-xs ${p.needed ? "border-violet-500" : "border-border opacity-70"}`}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={apiFileUrl(p.thumb_url)} alt="" className="h-10 w-8 rounded-sm bg-muted object-cover" />
              <span>
                <span className="block font-medium">✎ {p.priority} · {p.code}</span>
                <span className="block text-muted-foreground" data-testid={`portrait-status-${p.code}`}>
                  {p.needed ? STATUS_TEXT[p.status] : "reserve"}
                  {p.status === "drawing" ? ` ${Math.round(p.progress * 100)} %` : ""}
                  {p.error ? `: ${p.error}` : ""}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
