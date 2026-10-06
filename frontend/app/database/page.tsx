"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDate, formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/page-header";
import { ErrorState } from "@/components/state";
import { CaseStatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TR } from "@/components/ui/table";

export default function DatabasePage() {
  const { data, error, loading, refetch } = useApi(() => api.dbOverview());

  return (
    <div>
      <PageHeader
        title="Database"
        description="Internal view — what the system has stored and remembers."
      />

      {loading && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-56" />
          ))}
        </div>
      )}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Section title="Cases" count={data.cases.count}>
            <Table>
              <TBody>
                {data.cases.items.map((c) => (
                  <TR key={c.id}>
                    <TD>
                      <Link href={`/cases/${c.id}`} className="font-medium hover:text-primary">
                        {c.title}
                      </Link>
                    </TD>
                    <TD>
                      <CaseStatusBadge status={c.status} />
                    </TD>
                    <TD className="text-right text-muted-foreground">{formatDate(c.created_at)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Section>

          <Section title="Sources" count={data.sources.count}>
            <Table>
              <TBody>
                {data.sources.items.map((s) => (
                  <TR key={s.id}>
                    <TD className="max-w-52">
                      <Link href={`/cases/${s.case_id}`} className="block truncate font-medium hover:text-primary">
                        {s.title}
                      </Link>
                    </TD>
                    <TD>
                      <Badge variant="outline">{s.source_type}</Badge>
                    </TD>
                    <TD className="text-right text-muted-foreground">{s.publisher ?? "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Section>

          <Section title="Facts" count={data.facts.count}>
            <Table>
              <TBody>
                {data.facts.items.map((f) => (
                  <TR key={f.id}>
                    <TD className="max-w-72">
                      <Link href={`/cases/${f.case_id}`} className="block truncate hover:text-primary" dir="auto">
                        {f.claim}
                      </Link>
                    </TD>
                    <TD>
                      <Badge variant="outline">{f.category}</Badge>
                    </TD>
                    <TD className="text-right tabular-nums">{Math.round(f.confidence * 100)}%</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Section>

          <Section title="Contradictions" count={data.contradictions.count}>
            <Table>
              <TBody>
                {data.contradictions.items.map((c) => (
                  <TR key={c.id}>
                    <TD className="max-w-72">
                      <Link href={`/cases/${c.case_id}`} className="block truncate hover:text-primary" dir="auto">
                        {c.topic}
                      </Link>
                    </TD>
                    <TD className="text-right">
                      <Badge
                        variant={c.severity === "high" ? "danger" : c.severity === "medium" ? "warning" : "default"}
                      >
                        {c.severity}
                      </Badge>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Section>

          <Section title="Stories" count={data.stories.count}>
            <Table>
              <TBody>
                {data.stories.items.map((s) => (
                  <TR key={s.id}>
                    <TD>
                      <Link href={`/cases/${s.case_id}`} className="font-medium hover:text-primary">
                        Case #{s.case_id} — v{s.version}
                      </Link>
                    </TD>
                    <TD className="text-right tabular-nums">{Math.round(s.engagement_score)}</TD>
                    <TD className="text-right text-muted-foreground">{formatDateTime(s.created_at)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Section>

          <Section title="Discovery History" count={data.discovery_history.count}>
            <Table>
              <TBody>
                {data.discovery_history.items.map((d) => (
                  <TR key={d.id}>
                    <TD className="max-w-72">
                      <span className="block truncate" dir="auto">{d.title}</span>
                    </TD>
                    <TD className="text-right">
                      {d.selected ? (
                        <Badge variant="success">investigated</Badge>
                      ) : d.rejected ? (
                        <Badge variant="outline">ignored</Badge>
                      ) : (
                        <Badge variant="default">seen</Badge>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Section>

          <Card>
            <CardHeader>
              <CardTitle>Agent Runs</CardTitle>
              <span className="text-xs text-muted-foreground tabular-nums">{data.agent_runs.count} total</span>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Every discovery, research, writing, and critique execution is recorded persistently. See the{" "}
                <Link href="/research" className="text-primary hover:underline">
                  Research
                </Link>{" "}
                page for the full run log.
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <span className="text-xs text-muted-foreground tabular-nums">{count} rows</span>
      </CardHeader>
      <CardContent className="max-h-72 overflow-auto p-0">
        {count === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">Empty.</p>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}
