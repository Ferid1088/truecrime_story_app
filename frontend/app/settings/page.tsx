"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/page-header";
import { ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";

const ROLE_LABELS: Record<string, string> = {
  fact_extractor: "Fact Extractor",
  timeline_builder: "Timeline Builder",
  contradiction_analyzer: "Contradiction Analyzer",
  discovery: "Discovery",
  story_director: "Story Director",
  writer: "Writer",
  rewriter: "Rewriter",
  engagement_critic: "Engagement Critic",
  final_editor: "Final Editor",
};

const ALIAS_LABELS: Record<string, string> = {
  research_intelligence: "Research Intelligence",
  cheap: "Cheap",
  writer: "Writer",
  premium: "Premium",
  embedding: "Embedding",
};

const ALIAS_PURPOSES: Record<string, string> = {
  research_intelligence: "Query planning, reranking, gap analysis",
  cheap: "Structured extraction, facts, timeline, contradictions",
  writer: "Story direction, long-form writing, rewriting",
  premium: "Engagement critique, final editorial review",
  embedding: "Multilingual semantic retrieval & dedupe",
};

export default function SettingsPage() {
  const { data, error, loading, refetch } = useApi(() => api.settingsStatus());
  const { data: engine } = useApi(() => api.searchEngineStatus());
  const { data: apimaster } = useApi(() => api.apimasterStatus());

  const integrationBadge = (s?: { configured: boolean; reachable: boolean } | null) =>
    s == null
      ? undefined
      : s.configured && s.reachable
        ? { label: "Connected", variant: "success" as const }
        : s.configured
          ? { label: "Unreachable", variant: "warning" as const }
          : { label: "Not configured", variant: "warning" as const };

  return (
    <div>
      <PageHeader title="Settings" description="Provider and integration status." />

      {loading && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      )}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <StatusCard
            title="TrueCrime Search Engine"
            ok={!!engine?.configured && !!engine.reachable}
            badge={
              engine == null
                ? undefined
                : engine.status === "backend_unreachable"
                  ? { label: "Backend unreachable", variant: "danger" as const }
                  : integrationBadge(engine)
            }
            rows={[
              ["Engine", "First-party search / fetch / index"],
              [
                "Search backend",
                engine == null
                  ? "—"
                  : engine.search_backend?.healthy
                    ? `searxng · healthy`
                    : `searxng · ${engine.search_backend?.reason ?? "unreachable"}`,
              ],
              [
                "Backend URL",
                engine?.search_backend?.base_url ?? "—",
              ],
              [
                "Fetcher",
                engine?.fetcher?.configured ? "configured" : "—",
              ],
              [
                "Embedding",
                engine == null
                  ? "—"
                  : engine.embedding?.configured
                    ? `${engine.embedding.model ?? "configured"} · ${engine.embedding.cache_hits ?? 0} cached`
                    : "not configured",
              ],
              [
                "Intelligence",
                engine == null
                  ? "—"
                  : engine.llm?.configured
                    ? `${engine.llm.provider ?? "apimaster"} · query planning / rerank / gaps`
                    : `${engine.llm?.provider ?? "apimaster"} · not configured`,
              ],
            ]}
            hint={
              !engine?.configured
                ? "Set TRUECRIME_SEARXNG_URL in .env"
                : !engine.reachable
                  ? "SearXNG is not responding — research cannot run."
                  : "Web discovery runs on our own SearXNG + fetcher + index. APIMaster supplies intelligence only."
            }
          />
          <StatusCard
            title="Generation Provider"
            ok={
              !!apimaster?.configured &&
              !!apimaster.reachable &&
              apimaster.authorized === true &&
              (apimaster.models_missing?.length ?? 0) === 0
            }
            badge={
              apimaster == null
                ? undefined
                : apimaster.status === "missing_key"
                  ? { label: "Missing key", variant: "warning" as const }
                  : apimaster.status === "unauthorized" ||
                      apimaster.status === "forbidden"
                    ? { label: "Unauthorized", variant: "danger" as const }
                    : apimaster.status === "models_missing"
                      ? { label: "Models missing", variant: "danger" as const }
                      : integrationBadge(apimaster)
            }
            rows={[
              ["Provider", "APIMaster"],
              ["Base URL", data.generation.base_url ?? "—"],
              ["Role", "Reasoning, extraction, critique & writing"],
              ...Object.entries(data.generation.models).map(
                ([alias, modelId]): [string, string] => [
                  ALIAS_LABELS[alias] ?? alias,
                  modelId +
                    (apimaster && !apimaster.models_available.includes(modelId)
                      ? " ✗"
                      : ""),
                ],
              ),
            ]}
            hint={
              !apimaster?.configured
                ? "Set TrueCrime_APIMASTER_API_KEY in .env"
                : !apimaster.reachable
                  ? "Key is set but APIMaster did not respond."
                  : apimaster.status === "unauthorized" || apimaster.status === "forbidden"
                    ? "The API key was rejected — check TrueCrime_APIMASTER_API_KEY."
                    : apimaster.status === "models_missing"
                      ? `Not on APIMaster: ${apimaster.models_missing.join(", ")}`
                      : "All non-search LLM work runs through APIMaster."
            }
          />

          <Card className="md:col-span-2">
            <CardHeader>
              <CardTitle>Model Routing</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {Object.entries(data.generation.models).map(([alias, modelId]) => (
                  <div key={alias} className="rounded-md border border-border p-3">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      {ALIAS_LABELS[alias] ?? alias}
                    </p>
                    <p className="mt-1 break-all font-mono text-xs">{modelId}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {ALIAS_PURPOSES[alias] ?? ""}
                    </p>
                    {data.generation.fallbacks[alias]?.length ? (
                      <p className="mt-1 text-[10px] text-muted-foreground/70">
                        fallback → {data.generation.fallbacks[alias].join(" → ")}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>

              <div>
                <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Role assignment
                </p>
                <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(data.generation.routing).map(([role, alias]) => (
                    <div
                      key={role}
                      className="flex items-center justify-between gap-2 rounded-md border border-border px-2.5 py-1.5 text-xs"
                    >
                      <span className="min-w-0 text-muted-foreground">{ROLE_LABELS[role] ?? role}</span>
                      <span className="flex min-w-0 items-center gap-1.5">
                        <Badge variant={alias === "premium" ? "accent" : alias === "writer" ? "info" : "default"}>
                          {ALIAS_LABELS[alias] ?? alias}
                        </Badge>
                        <span className="min-w-0 max-w-36 truncate font-mono text-[10px] text-muted-foreground/60">
                          {data.generation.models[alias]}
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          {data.multilingual && (
          <Card>
            <CardHeader>
              <CardTitle>Multilingual</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5">
              {(
                [
                  ["Research languages", data.multilingual.research_languages],
                  ["Canonical language", [data.multilingual.canonical_language]],
                  ["Localization targets", data.multilingual.localization_languages],
                ] as [string, string[]][]
              ).map(([label, langs]) => (
                <div key={label} className="flex items-center justify-between gap-4 text-sm">
                  <span className="text-muted-foreground">{label}</span>
                  <span className="flex flex-wrap justify-end gap-1">
                    {langs.map((l) => (
                      <Badge key={l} variant="outline">
                        {langLabel(l)}
                      </Badge>
                    ))}
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
          )}

          <StatusCard
            title="Search Provider"
            ok={data.search.configured}
            rows={[["Provider", data.search.provider]]}
            hint={
              data.search.configured
                ? undefined
                : "Set SEARCH_PROVIDER=tavily and TAVILY_API_KEY in .env"
            }
          />
          <StatusCard
            title="YouTube"
            ok={data.youtube.configured}
            rows={[["Integration", "Video metadata search"]]}
            hint={data.youtube.configured ? undefined : "Set YOUTUBE_API_KEY in .env"}
          />
          <StatusCard
            title="Database"
            ok={data.database.connected}
            rows={[["Engine", data.database.url]]}
          />
        </div>
      )}

      <p className="mt-6 text-xs text-muted-foreground">
        API keys are never sent to the browser — only their configured status.
      </p>
    </div>
  );
}

function StatusCard({
  title,
  ok,
  rows,
  hint,
  badge,
}: {
  title: string;
  ok: boolean;
  rows: [string, string][];
  hint?: string;
  badge?: { label: string; variant: "success" | "warning" | "danger" };
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {badge ? (
          <Badge variant={badge.variant}>
            {badge.variant === "success" ? (
              <CheckCircle2 className="size-3" />
            ) : (
              <XCircle className="size-3" />
            )}{" "}
            {badge.label}
          </Badge>
        ) : ok ? (
          <Badge variant="success">
            <CheckCircle2 className="size-3" /> Connected
          </Badge>
        ) : (
          <Badge variant="warning">
            <XCircle className="size-3" /> Not configured
          </Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-1.5">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4 text-sm">
            <span className="shrink-0 text-muted-foreground">{k}</span>
            <span className="min-w-0 truncate font-mono text-xs">{v}</span>
          </div>
        ))}
        {hint && <p className="pt-1 text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  );
}
