"use client";

import type { StoryFull } from "@/lib/types";
import { estimateMinutes, isRtl, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Fragment } from "react";

/** Lines that are pure structural residue, never narration. */
const RESIDUE = /^(#{1,6}\s*|(-{3,}|_{3,}|\*{3,})\s*|(ACT|Act|CHAPTER|Chapter|SECTION|Section)\s+[IVX\d]+[.:]?\s*)$/;

/**
 * Render a paragraph with light inline markup: *italic*, **bold**.
 * Story text is stored as plain narration; this only formats what the
 * model emitted — nothing is invented.
 */
function InlineText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).filter(Boolean);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**") && p.length > 4) {
          return <strong key={i}>{p.slice(2, -2)}</strong>;
        }
        if (p.startsWith("*") && p.endsWith("*") && p.length > 2) {
          return <em key={i}>{p.slice(1, -1)}</em>;
        }
        return <Fragment key={i}>{p}</Fragment>;
      })}
    </>
  );
}

export function StoryReader({ story }: { story: StoryFull }) {
  const rtl = isRtl(story.language);
  const paragraphs = story.story_text
    .split(/\n{2,}|\n/)
    .map((p) => p.trim())
    .filter((p) => p && !RESIDUE.test(p));

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border pb-3 text-xs text-muted-foreground">
        <Badge variant="accent">v{story.version}</Badge>
        <span>{langLabel(story.language)}</span>
        <span className="tabular-nums">{story.word_count.toLocaleString()} words</span>
        <span className="tabular-nums">≈ {estimateMinutes(story.word_count)} min narration</span>
        <span>
          Engagement <span className="font-medium text-foreground">{Math.round(story.engagement_score)}</span>
        </span>
        <span>
          Similarity{" "}
          <span className="font-medium text-foreground">
            {story.similarity_score == null ? "n/e" : `${(story.similarity_score * 100).toFixed(0)}%`}
          </span>
        </span>
        {story.status !== "ready" && (
          <Badge variant="warning">{story.status.replace("_", " ")}</Badge>
        )}
      </div>
      <article
        dir={rtl ? "rtl" : "ltr"}
        className="max-w-prose text-[15px] leading-8 text-foreground/95"
      >
        {paragraphs.map((p, i) => (
          <p key={i} className="mb-5">
            <InlineText text={p} />
          </p>
        ))}
      </article>
    </div>
  );
}
