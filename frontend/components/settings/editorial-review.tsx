"use client";

import { useState } from "react";
import { ScanSearch } from "lucide-react";
import { editorialReviewApi, ApiError } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

export function EditorialReviewCard() {
  const overview = useApi(() => editorialReviewApi.overview(), [], { keepPrevious: true });
  const [verdict, setVerdict] = useState<string>("");
  const decisions = useApi(() => editorialReviewApi.decisions(verdict || undefined), [verdict], {
    keepPrevious: true,
  });
  const [message, setMessage] = useState<string | null>(null);
  if (!overview.data) return null;

  async function toggle(enabled: boolean) {
    setMessage(null);
    try {
      await editorialReviewApi.setEnabled(enabled);
      overview.refetch();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Could not save.");
    }
  }

  const counts = overview.data.counts;
  return (
    <Card className="mt-6" data-testid="editorial-review-card">
      <CardContent className="space-y-3 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <ScanSearch className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">Automatic photo review</h2>
          <span className="text-xs text-muted-foreground">
            approved {counts.approved ?? 0} · rejected {counts.rejected ?? 0} · waiting {overview.data.waiting}
          </span>
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={overview.data.enabled}
            onChange={(e) => toggle(e.target.checked)}
          />
          <span>
            Review pictures with unclear rights automatically
            <span className="block text-xs text-muted-foreground">
              An independent vision reviewer approves a picture only on a clear pass (right person, place and
              period; identity supported; caption matches) and rejects on any doubt. Rights and identity gates
              stay as they are; every decision is kept below.
            </span>
          </span>
        </label>
        {message && <p className="text-xs text-destructive">{message}</p>}
        <div className="flex gap-2 text-xs">
          {["", "approved", "rejected"].map((v) => (
            <button
              key={v || "all"}
              type="button"
              onClick={() => setVerdict(v)}
              className={`rounded-md border px-2 py-1 ${verdict === v ? "border-primary" : "border-border"}`}
            >
              {v || "all"}
            </button>
          ))}
        </div>
        <ul className="space-y-2 text-xs">
          {(decisions.data ?? []).map((d) => (
            <li key={d.id} className="flex gap-3 border-t border-border pt-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={d.thumb_url} alt="" className="h-16 w-24 shrink-0 rounded object-cover" />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono">{d.asset_code}</span>
                  <Badge variant={d.verdict === "approved" ? "success" : "danger"}>{d.verdict}</Badge>
                  <span className="text-muted-foreground">{d.model}</span>
                  <span className="text-muted-foreground">{new Date(d.created_at).toLocaleString()}</span>
                </div>
                <p className="truncate">{d.title}</p>
                <p className="text-muted-foreground">{d.reasons.join(" · ")}</p>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
