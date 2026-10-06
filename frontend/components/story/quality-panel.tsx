"use client";

import type { StoryMeta } from "@/lib/types";
import { Progress } from "@/components/ui/progress";

const DIMENSIONS: { key: string; label: string }[] = [
  { key: "hook", label: "Hook" },
  { key: "pacing", label: "Pacing" },
  { key: "curiosity", label: "Curiosity" },
  { key: "clarity", label: "Clarity" },
  { key: "emotional_stakes", label: "Emotional Stakes" },
  { key: "reveal_timing", label: "Reveal Timing" },
  { key: "ending", label: "Ending" },
  { key: "repetition", label: "Repetition" },
];

function barTone(v: number) {
  if (v >= 80) return "bg-emerald-500";
  if (v >= 60) return "bg-amber-500";
  return "bg-rose-500";
}

export function QualityPanel({ story }: { story: StoryMeta }) {
  const dims = story.dimensions ?? {};
  const hasDims = Object.keys(dims).length > 0;

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1 flex items-baseline justify-between">
          <p className="text-xs font-medium text-muted-foreground">Overall Engagement</p>
          <p className="text-2xl font-semibold tabular-nums">{Math.round(story.engagement_score)}</p>
        </div>
        <Progress value={story.engagement_score} barClassName={barTone(story.engagement_score)} />
      </div>

      {hasDims ? (
        <div className="space-y-2.5">
          {DIMENSIONS.map(({ key, label }) => {
            const v = dims[key];
            if (v == null) return null;
            return (
              <div key={key}>
                <div className="mb-0.5 flex justify-between text-xs">
                  <span className="text-muted-foreground">{label}</span>
                  <span className="font-medium tabular-nums">{Math.round(v)}</span>
                </div>
                <Progress value={v} className="h-1" barClassName={barTone(v)} />
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No per-dimension scores for this version.</p>
      )}

      {story.problems.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">Critic feedback</p>
          <ul className="space-y-1 text-xs leading-5 text-foreground/80">
            {story.problems.map((p, i) => (
              <li key={i} className="rounded border border-border bg-subtle px-2 py-1.5">
                {p}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
