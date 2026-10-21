"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Circle, Loader2, OctagonAlert } from "lucide-react";
import { ApiError, autopilotApi, type AutopilotRunView, type PublishRequestRow } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, humanize, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { PageHeader } from "@/components/page-header";

const POLL_MS = 5000;
const ACTIVE = ["running", "waiting_owner"];
const STATUS_VARIANT = {
  running: "info",
  waiting_owner: "warning",
  completed: "success",
  cancelled: "outline",
} as const;
const STEP_LABEL: Record<string, string> = {
  case: "Case opened",
  research: "Research and facts",
  texts: "Texts in every language",
  films: "Films",
  titles: "Title suggestions",
  shorts: "Shorts and reels",
  publish: "Dry-run publish queue",
};
const eur = (n: number) => `€${n.toFixed(2)}`;

export function RunPage({ runId }: { runId: number }) {
  const { data: run, error, refetch } = useApi(() => autopilotApi.run(runId), [runId], { keepPrevious: true });
  const [cancelError, setCancelError] = useState<string | null>(null);
  const active = run != null && ACTIVE.includes(run.status);

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(refetch, POLL_MS);
    return () => clearInterval(timer);
  }, [active, refetch]);

  async function cancel() {
    setCancelError(null);
    try {
      await autopilotApi.cancel(runId);
      refetch();
    } catch (e) {
      setCancelError(e instanceof ApiError ? e.message : String(e));
    }
  }

  if (!run) return <p className="text-sm text-muted-foreground">{error ?? "Loading the run…"}</p>;
  const blocking = run.needs_you.filter((n) => n.blocking);
  return (
    <div className="space-y-4" data-testid="autopilot-run">
      <PageHeader
        title={run.case.title}
        description={`Autopilot run ${run.id} — started ${formatDateTime(run.created_at)}`}
        actions={
          <>
            <Badge variant={STATUS_VARIANT[run.status]} data-testid="run-status">{humanize(run.status)}</Badge>
            {active && (
              <Button size="sm" variant="outline" onClick={cancel} data-testid="run-cancel">
                Cancel run
              </Button>
            )}
          </>
        }
      />
      {cancelError && <p role="alert" className="text-xs text-rose-600">{cancelError}</p>}
      <BudgetCard run={run} />
      <NeedsYou run={run} blocking={blocking.length} />
      <Steps run={run} />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {Object.entries(run.languages).map(([lang, v]) => (
          <Card key={lang} data-testid={`run-language-${lang}`}>
            <CardHeader>
              <CardTitle className="text-sm">{langLabel(lang)}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              {v.film_job ? (
                <div>
                  <div className="mb-1 flex justify-between">
                    <span>
                      Film {humanize(v.film_job.status)}
                      {v.film_job.stage ? ` — ${humanize(v.film_job.stage)}` : ""}
                    </span>
                    <span>{Math.round(v.film_job.progress * 100)}%</span>
                  </div>
                  <Progress value={v.film_job.progress * 100} />
                </div>
              ) : (
                <p className="text-muted-foreground">Film not started yet.</p>
              )}
              <div>
                <p className="mb-1 font-medium">Title suggestions ({v.titles.length})</p>
                {v.titles.length ? (
                  <ol className="list-decimal space-y-0.5 pl-4" dir={lang === "fa" || lang === "ar" ? "rtl" : "ltr"}>
                    {v.titles.map((t) => (
                      <li key={t.id}>{t.title}</li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-muted-foreground">None yet.</p>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <PublishPanel run={run} />
    </div>
  );
}

function BudgetCard({ run }: { run: AutopilotRunView }) {
  const { cap_eur, spent_eur } = run.budget;
  return (
    <Card>
      <CardContent className="space-y-1.5 pt-4 text-sm" data-testid="run-budget">
        <div className="flex justify-between">
          <span>
            Spent {eur(spent_eur)} of the {eur(cap_eur)} cap
          </span>
          {run.estimate.estimated_eur != null && (
            <span className="text-muted-foreground">Estimated {eur(run.estimate.estimated_eur)}</span>
          )}
        </div>
        <Progress value={(spent_eur / cap_eur) * 100} />
      </CardContent>
    </Card>
  );
}

function NeedsYou({ run, blocking }: { run: AutopilotRunView; blocking: number }) {
  return (
    <Card data-testid="needs-you">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <OctagonAlert className="size-4" aria-hidden /> Needs you
          {blocking > 0 && <Badge variant="warning">{blocking} blocking</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {run.needs_you.length === 0 ? (
          <p className="text-xs text-muted-foreground">Nothing needs you right now.</p>
        ) : (
          <ul className="space-y-1.5 text-sm">
            {run.needs_you.map((n) => (
              <li key={n.key} className="flex items-start gap-2">
                <Badge variant={n.blocking ? "warning" : "outline"}>{n.blocking ? "blocking" : "later"}</Badge>
                <span>
                  {n.language && <span className="font-medium">{langLabel(n.language)}: </span>}
                  {n.message}
                </span>
              </li>
            ))}
          </ul>
        )}
        {run.needs_you.length > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            The autopilot never approves these for you. Open the{" "}
            <Link className="underline" href={`/cases/${run.case.id}`}>case</Link> to act; the run carries on by itself.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function Steps({ run }: { run: AutopilotRunView }) {
  return (
    <Card>
      <CardContent className="pt-4">
        <ol className="space-y-2" data-testid="run-steps">
          {run.steps.map((s) => (
            <li key={s.name} className="flex items-start gap-2 text-sm" data-testid={`step-${s.name}`}>
              {s.status === "done" ? (
                <CheckCircle2 className="mt-0.5 size-4 text-emerald-600" aria-label="done" />
              ) : s.status === "waiting" ? (
                <OctagonAlert className="mt-0.5 size-4 text-amber-600" aria-label="needs you" />
              ) : s.status === "running" ? (
                <Loader2 className="mt-0.5 size-4 animate-spin text-sky-600" aria-label="running" />
              ) : (
                <Circle className="mt-0.5 size-4 text-muted-foreground" aria-label="waiting" />
              )}
              <div>
                <p className="font-medium">{STEP_LABEL[s.name] ?? s.name}</p>
                {s.detail && <p className="text-xs text-muted-foreground">{s.detail}</p>}
              </div>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function PublishPanel({ run }: { run: AutopilotRunView }) {
  const { data } = useApi(() => autopilotApi.publishRequests(run.id), [run.id, run.publish_requests], {
    keepPrevious: true,
  });
  const [open, setOpen] = useState<PublishRequestRow | null>(null);
  return (
    <Card data-testid="publish-queue">
      <CardHeader>
        <CardTitle className="text-sm">Publish queue — dry run</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="mb-2 text-xs text-muted-foreground">
          Exact requests per language and platform, with their schedule. Nothing is sent: no channel is connected.
        </p>
        {!data || data.requests.length === 0 ? (
          <p className="text-xs text-muted-foreground">The queue is built after the films, titles and shorts are done.</p>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1">When (UTC)</th>
                <th>Language</th>
                <th>Platform</th>
                <th>Kind</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.requests.map((r) => (
                <tr key={r.id} className="border-t border-border">
                  <td className="py-1">{formatDateTime(r.scheduled_for)}</td>
                  <td>{langLabel(r.language)}</td>
                  <td>{humanize(r.platform)}</td>
                  <td>{r.kind}</td>
                  <td>
                    <Badge variant="outline">{humanize(r.status)}</Badge>
                  </td>
                  <td>
                    <Button size="sm" variant="outline" onClick={() => setOpen(r)}>
                      Request
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
      <Dialog
        open={open != null}
        onClose={() => setOpen(null)}
        title={open ? `${langLabel(open.language)} — ${humanize(open.platform)}` : ""}
        description="The exact request that would be sent. It is not sent."
        className="max-w-2xl"
      >
        <pre className="max-h-96 overflow-auto rounded bg-muted p-3 text-xs">{JSON.stringify(open?.request, null, 2)}</pre>
      </Dialog>
    </Card>
  );
}
