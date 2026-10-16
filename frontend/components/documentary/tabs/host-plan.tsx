"use client";

import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { HostScene } from "@/lib/types";

const PERSONA_VARIANT = { current: "outline", "persona changed": "warning", "unknown persona": "warning" } as const;

/** The host plan as placed in the film, with each segment's dialogue, the critic's
 *  verdict, the persona hash and the direction field by field (executed or planned only). */
export function HostPlanPanel({ caseId, scenes }: { caseId: number; scenes: HostScene[] }) {
  const { data: plan, error } = useApi(() => api.hostPlan(caseId), [caseId], { keepPrevious: true });
  const versionId = scenes[0]?.story_version_id;
  const { data: segs } = useApi(
    () => (versionId ? api.hostSegments(versionId) : Promise.resolve(null)),
    [versionId],
    { keepPrevious: true },
  );
  if (error && !plan) return null;
  if (!plan) return null;
  const dialogue = new Map((segs?.segments ?? []).map((s) => [s.id, s]));
  const sceneBy = new Map(scenes.map((s) => [s.host_segment_id, s]));
  return (
    <Card data-testid="host-plan">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          Host plan v{plan.version}
          <Badge variant={plan.validation.status === "valid" ? "success" : "warning"}>{plan.validation.status}</Badge>
          <Badge variant={PERSONA_VARIANT[plan.persona_state]} title={plan.persona_sha256 ?? ""}>
            persona {plan.persona_state === "current" ? plan.persona_sha256?.slice(0, 8) : plan.persona_state}
          </Badge>
          {plan.validation.host_seconds != null && (
            <span className="text-xs font-normal text-muted-foreground">{plan.validation.host_seconds} s planned</span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 p-3 text-xs">
        {plan.plan.segments.map((seg) => {
          const d = dialogue.get(seg.id);
          const scene = sceneBy.get(seg.id);
          return (
            <div key={seg.id} className="rounded-md border border-border p-2.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium">{seg.id}</span>
                <Badge variant="outline">{seg.position}{seg.beat_id ? ` after ${seg.beat_id}` : " before beat 1"}</Badge>
                <span className="text-muted-foreground">{seg.target_seconds} s planned</span>
                {seg.memory_reference && <Badge variant="info">memory {seg.memory_reference}</Badge>}
                {d && (
                  <Badge variant={d.quality.pass ? "success" : "danger"}>
                    critic {d.quality.pass ? "passed" : "failed"}
                  </Badge>
                )}
                {scene && <Badge variant="outline">scene {scene.status.replace("_", " ")}</Badge>}
              </div>
              <p className="mt-1 text-muted-foreground">{seg.purpose}</p>
              {d && <p dir="auto" className="mt-1">{d.avatar_dialogue}</p>}
              {scene && scene.direction_execution.length > 0 && (
                <ul className="mt-2 grid grid-cols-1 gap-x-4 gap-y-0.5 sm:grid-cols-2">
                  {scene.direction_execution.map((f) => (
                    <li key={f.field} className="flex items-center gap-1.5">
                      <Badge variant={f.executed ? "success" : "warning"}>{f.shown_as.replace(/_/g, " ").toLowerCase()}</Badge>
                      <span className="text-muted-foreground">{f.field.replace(/_/g, " ")}:</span>
                      <span>{String(f.value ?? "—")}</span>
                    </li>
                  ))}
                </ul>
              )}
              {scene && !scene.direction && (
                <p className="mt-1 text-muted-foreground">No stage direction yet; it is made after the voice.</p>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
