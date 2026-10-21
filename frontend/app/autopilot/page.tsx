"use client";

import Link from "next/link";
import { autopilotApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, humanize } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/state";

export default function AutopilotPage() {
  const { data } = useApi(() => autopilotApi.runs(), [], { keepPrevious: true });
  return (
    <div>
      <PageHeader title="Autopilot" description="Runs started with the Start button on a discovered case." />
      {data && data.runs.length === 0 && (
        <EmptyState title="No runs yet" description="Press Start on a case in Discover." />
      )}
      <div className="space-y-2">
        {data?.runs.map((r) => (
          <Link key={r.id} href={`/autopilot/${r.id}`} className="block">
            <Card>
              <CardContent className="flex items-center justify-between gap-3 pt-4 text-sm">
                <span className="font-medium">{r.case.title}</span>
                <span className="flex items-center gap-3 text-xs text-muted-foreground">
                  {formatDateTime(r.created_at)}
                  <Badge variant="outline">{humanize(r.status)}</Badge>
                </span>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
