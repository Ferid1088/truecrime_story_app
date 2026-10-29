"use client";

import { detectiveApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { DetectiveScene } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DocumentaryTabProps } from "../shared";

function SceneCard({ scene }: { scene: DetectiveScene }) {
  return (
    <Card data-testid={`detective-scene-${scene.scene_key}`}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          Scene {scene.scene_key} · {scene.mode}
          {scene.stale && (
            <Badge variant="warning" data-testid="scene-stale-badge">
              stale — {scene.stale_reason ?? "needs recheck"}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p>
          <span className="font-medium">Aim:</span> {scene.aim}
        </p>
        <p>
          <span className="font-medium">Change in understanding:</span> {scene.change_in_understanding}
        </p>
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Expected gain:</span> {scene.expected_audience_gain}
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3">Tool</th>
                <th className="pr-3">Purpose</th>
                <th className="pr-3">Content bindings</th>
                <th>Reuse reason</th>
              </tr>
            </thead>
            <tbody>
              {scene.actions.map((action) => (
                <tr key={action.action_key} className="border-t align-top">
                  <td className="py-1 pr-3 font-mono">{action.tool_id}</td>
                  <td className="pr-3">{action.purpose}</td>
                  <td className="pr-3 font-mono">{action.content_bindings.join(", ") || "–"}</td>
                  <td>{action.reuse_reason ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

export function DetectiveTab({ caseId, refreshKey }: DocumentaryTabProps) {
  const plan = useApi(() => detectiveApi.plan(caseId), [caseId, refreshKey], { keepPrevious: true });
  return (
    <div className="space-y-4" data-testid="detective-tab">
      <p className="max-w-3xl text-xs leading-5 text-muted-foreground">
        The Detective Director is one of the Main Visual Planner&apos;s own tools: for each scene it
        proposes an investigative aim, a tool sequence, and why that sequence was chosen. The Main
        Visual Planner records whether it accepted, combined, deferred, or rejected each proposed
        action — only an accepted or combined action ever reaches a real shot.
      </p>
      {plan.error && !plan.data && (
        <p className="text-sm text-muted-foreground" data-testid="detective-tab-empty">
          {plan.error}
        </p>
      )}
      {plan.data && (
        <>
          <div className="flex flex-wrap gap-2 text-xs" data-testid="detective-totals">
            <Badge variant="outline">scenes: {plan.data.scene_count}</Badge>
            <Badge variant={plan.data.stale_count > 0 ? "warning" : "success"}>
              stale: {plan.data.stale_count}
            </Badge>
          </div>
          {plan.data.scenes.map((scene) => (
            <SceneCard key={scene.scene_key} scene={scene} />
          ))}
        </>
      )}
    </div>
  );
}
