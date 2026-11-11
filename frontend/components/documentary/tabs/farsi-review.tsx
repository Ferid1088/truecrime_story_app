"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/state";
import { BlockReview } from "./block-review";
import type { ReviewKind } from "./block-calls";
import type { LanguageTabProps } from "../shared";

const LANGUAGE = "fa";

/** The views of the Persian owner's review: the long film's narration (with assembling and approval) and the voices and texts of the host, the film's opening and the short clips. */
const VIEWS: { kind: ReviewKind; label: string; empty: string }[] = [
  { kind: "narration", label: "Long film voice", empty: "The voice review opens when the Persian version has a narration." },
  { kind: "host", label: "Host voice", empty: "The host voice review opens when the Persian version has a narration." },
  { kind: "opening", label: "Opening (hook)", empty: "The opening review opens when the Persian version has a narration." },
  { kind: "clips", label: "Short clips", empty: "The clips review opens when the Persian version has a narration." },
];

/** Every hand review of the Persian film in one place: listen, correct the text and the voice, tick the take that counts. */
export function FarsiReviewTab({ overview }: LanguageTabProps) {
  const versionId = overview.languages[LANGUAGE]?.version_id ?? null;
  const [view, setView] = useState<ReviewKind>("narration");
  const current = VIEWS.find((v) => v.kind === view) ?? VIEWS[0];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Farsi review</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Persian reviews">
          {VIEWS.map((v) => (
            <Button key={v.kind} size="sm" variant={view === v.kind ? "default" : "secondary"} onClick={() => setView(v.kind)} data-testid={`farsi-review-${v.kind}`}>
              {v.label}
            </Button>
          ))}
        </div>
        {versionId != null ? (
          <BlockReview key={`${view}-${versionId}`} versionId={versionId} kind={view} />
        ) : (
          <EmptyState title="No Persian narration yet" description={current.empty} />
        )}
      </CardContent>
    </Card>
  );
}
