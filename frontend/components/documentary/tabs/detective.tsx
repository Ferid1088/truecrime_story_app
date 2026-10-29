"use client";

import { useState } from "react";

import { detectiveApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { DetectiveScene } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const plan = useApi(() => detectiveApi.plan(caseId), [caseId, refreshKey], { keepPrevious: true });
  return (
    <div className="space-y-4" data-testid="detective-tab">
      <p className="max-w-3xl text-xs leading-5 text-muted-foreground">
        The Detective Toolkit plans the film from its text: first a strategy for the whole film, then for each scene an
        investigative aim and the cards that serve it. It builds every card and fixes it in the film&apos;s timeline;
        the Main Visual Planner fills the rest with pictures and videos and never moves a card.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          loading={busy}
          disabled={busy}
          data-testid="detective-reset"
          onClick={async () => {
            if (!window.confirm("Start the toolkit again? Its plan (strategy, scenes, cards) is removed and made again by "
              + "the next run. Every picture and video that was found and approved is kept.")) return;
            setBusy(true);
            try {
              const done = await detectiveApi.reset(caseId);
              setMessage(`Removed ${done.removed.scenes} scenes and ${done.removed.claims} claims. Start a run to plan the film again.`);
              plan.refetch();
            } catch (e) {
              setMessage(e instanceof Error ? e.message : "Could not start the toolkit again.");
            } finally {
              setBusy(false);
            }
          }}
        >
          Start the toolkit again
        </Button>
        {message && <span className="text-xs text-muted-foreground" role="status">{message}</span>}
      </div>
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
