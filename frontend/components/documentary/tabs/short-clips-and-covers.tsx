"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { LanguageTabProps } from "../shared";
import { CoversTab } from "./covers";
import { ShortClipsTab } from "./short-clips";

const VIEWS = [
  { key: "clips", label: "Short clips" },
  { key: "covers", label: "Short covers" },
] as const;

/** Everything about the shorts in one place: the clips of each language and their covers. */
export function ShortClipsAndCoversTab(props: LanguageTabProps) {
  const [view, setView] = useState<(typeof VIEWS)[number]["key"]>("clips");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Shorts">
        {VIEWS.map((v) => (
          <Button key={v.key} size="sm" variant={view === v.key ? "default" : "secondary"} onClick={() => setView(v.key)} data-testid={`shorts-view-${v.key}`}>
            {v.label}
          </Button>
        ))}
      </div>
      {view === "clips" ? <ShortClipsTab caseId={props.caseId} /> : <CoversTab {...props} which="shorts" />}
    </div>
  );
}
