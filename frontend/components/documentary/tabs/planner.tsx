"use client";

import Link from "next/link";
import { useState } from "react";
import { ApiError, mvpApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import type { MvpFilmReview, MvpRequirement } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DocumentaryTabProps } from "../shared";

const REVIEW_STEPS = ["IMPLEMENTING", "PRODUCING", "VALIDATING", "AWAITING_OWNER_REVIEW"] as const;

const KIND_LABEL: Record<string, string> = {
  conflict: "conflict pages",
  conflict_board: "conflict board",
  document: "document cards",
  floor_plan: "floor plans",
  headline: "newspaper headlines",
  key_facts: "key facts",
  quote: "quotes",
  recap: "recaps",
  timeline: "timelines",
};

/** What direction left empty after every picture, video and graphic, and which graphics filled the rest. */
function DirectionCard({ caseId, refreshKey }: { caseId: number; refreshKey: number }) {
  const gaps = useApi(() => mvpApi.gaps(caseId), [caseId, refreshKey], { keepPrevious: true });
  if (!gaps.data) return null;
  const { gap_count, gap_seconds, alternatives } = gaps.data;
  const used = Object.entries(alternatives).sort((a, b) => b[1] - a[1]);
  return (
    <Card data-testid="direction-card">
      <CardHeader>
        <CardTitle className="text-sm">Direction · {gaps.data.language.toUpperCase()}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>
          {gap_count === 0
            ? "Every moment of the film has a picture, a video or a graphic."
            : `${gap_count} moment(s), ${gap_seconds} s of the film, still have no picture, video or graphic. The film is not made until they do.`}
        </p>
        {used.length > 0 && (
          <div className="flex flex-wrap gap-2 text-xs" data-testid="direction-alternatives">
            {used.map(([kind, seconds]) => (
              <Badge key={kind} variant="outline">
                {KIND_LABEL[kind] ?? kind}: {Math.round(seconds)} s
              </Badge>
            ))}
          </div>
        )}
        {gap_count > 0 && (
          <Link className="inline-block text-sm underline" href={`/documentary/${caseId}/review`}>
            Review pictures
          </Link>
        )}
      </CardContent>
    </Card>
  );
}

export function PlannerTab({ caseId, refreshKey }: DocumentaryTabProps) {
  const plan = useApi(() => mvpApi.plan(caseId), [caseId, refreshKey], { keepPrevious: true });
  const reviews = useApi(() => mvpApi.reviews(caseId), [caseId, refreshKey], { keepPrevious: true });
  return (
    <div className="space-y-4" data-testid="planner-tab">
      <p className="max-w-3xl text-xs leading-5 text-muted-foreground">
        The Main Visual Planner decides what the viewer sees: requirements from the reveal graph, bounded
        research for what is missing, five separate checks on every candidate picture, then the shot list
        and the film itself. A rendered film stays a review candidate until you approve its exact file.
      </p>
      <DirectionCard caseId={caseId} refreshKey={refreshKey} />
      {(reviews.data ?? []).map((review) => (
        <FilmReviewCard key={review.id} review={review} onChange={reviews.refetch} />
      ))}
      {plan.error && !plan.data && <p className="text-sm text-muted-foreground">{plan.error}</p>}
      {plan.data && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              Requirements · reveal graph #{plan.data.reveal_graph_id ?? "–"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-2 text-xs" data-testid="planner-totals">
              {Object.entries(plan.data.candidate_totals).map(([status, n]) => (
                <Badge key={status} variant="outline">
                  {status.replace("_", " ")}: {n}
                </Badge>
              ))}
              <Badge variant={plan.data.shortages ? "danger" : "success"}>
                shortages: {plan.data.shortages}
              </Badge>
              {plan.data.candidate_totals.needs_review > 0 && (
                <Link className="text-xs underline" href={`/documentary/${caseId}/review`}>
                  Review {plan.data.candidate_totals.needs_review} open picture(s)
                </Link>
              )}
            </div>
            <RequirementTable requirements={plan.data.requirements} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function RequirementTable({ requirements }: { requirements: MvpRequirement[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-1 pr-3">Req</th>
            <th className="pr-3">Entity</th>
            <th className="pr-3">Function</th>
            <th className="pr-3">Needs</th>
            <th className="pr-3">Approved / candidates</th>
            <th className="pr-3">Rounds</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {requirements.map((r) => (
            <tr key={r.key} className="border-t align-top">
              <td className="py-1 pr-3 font-mono">{r.key}</td>
              <td className="pr-3">{r.entity ?? "–"}</td>
              <td className="pr-3">{r.function.replace("_", " ")}</td>
              <td className="pr-3">{r.role_needed}</td>
              <td className="pr-3">{r.approved} / {r.candidates}</td>
              <td className="pr-3" title={r.search_rounds.map((s) => s.query).join("\n")}>{r.rounds_used}</td>
              <td>
                <Badge variant={r.status === "shortage" ? "danger" : "success"}>{r.status}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FilmReviewCard({ review, onChange }: { review: MvpFilmReview; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const awaiting = review.state === "AWAITING_OWNER_REVIEW";
  const v = review.validation;

  async function decide(decision: "APPROVED" | "REVISION_REQUIRED" | "REJECTED") {
    if (!review.render_sha256) return;
    setBusy(true);
    setMessage(null);
    try {
      await mvpApi.decideFilm(review.id, decision, review.render_sha256, note || undefined);
      onChange();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Could not save the decision.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-testid={`film-review-${review.language}`}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          {review.language.toUpperCase()} film · owner review
          <Badge variant={awaiting ? "warning" : "default"} data-testid="film-review-state">
            {review.state}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <ol className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          {REVIEW_STEPS.map((step) => (
            <li key={step} className={step === review.state ? "font-semibold text-foreground" : ""}>
              {step.replace(/_/g, " ").toLowerCase()}
            </li>
          ))}
        </ol>
        {review.render_sha256 && (
          <p className="break-all font-mono text-xs text-muted-foreground">sha256 {review.render_sha256}</p>
        )}
        {v.file && (
          <p className="text-xs">
            {Math.round(v.file.duration / 60)} min · {v.file.width}×{v.file.height} @ {v.file.fps} fps ·{" "}
            {v.file.video_codec}/{v.file.audio_codec} · {v.file.black_seconds}s black
            {v.file.integrated_lufs != null && ` · ${v.file.integrated_lufs} LUFS`}
          </p>
        )}
        {v.coverage && (
          <p className="text-xs">
            Temporal coverage {(v.coverage.temporal_coverage * 100).toFixed(1)}% · weak sentences{" "}
            {v.coverage.weak_sentence_count}
          </p>
        )}
        {(v.blocking ?? []).length > 0 && (
          <ul className="list-disc pl-5 text-xs text-rose-700">
            {v.blocking!.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        )}
        {(v.publication_blockers ?? []).length > 0 && (
          <ul className="list-disc pl-5 text-xs text-amber-700">
            {v.publication_blockers!.map((b) => (
              <li key={b}>{b} (blocks publication, not this review)</li>
            ))}
          </ul>
        )}
        {awaiting && (
          <div className="space-y-2">
            <textarea
              className="w-full rounded border p-2 text-sm"
              rows={2}
              placeholder="Your note (optional)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <div className="flex flex-wrap gap-3">
              <Button type="button" disabled={busy} onClick={() => void decide("APPROVED")}>
                Approve this exact film
              </Button>
              <Button type="button" variant="outline" disabled={busy} onClick={() => void decide("REVISION_REQUIRED")}>
                Revision required
              </Button>
              <Button type="button" variant="ghost" disabled={busy} onClick={() => void decide("REJECTED")}>
                Reject
              </Button>
            </div>
          </div>
        )}
        {review.decided_at && (
          <p className="text-xs text-muted-foreground">
            Decided {formatDateTime(review.decided_at)}
            {review.owner_note ? ` — ${review.owner_note}` : ""}
          </p>
        )}
        {message && <p className="text-xs text-rose-700">{message}</p>}
      </CardContent>
    </Card>
  );
}
