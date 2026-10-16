"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const KINDS = ["opinion", "reaction", "correction", "open_question", "theme"] as const;

/** What the host remembers: read, correct, deactivate, add; and which plan
 *  segment used each memory. */
export function HostMemoryPanel({ caseId }: { caseId: number }) {
  const [tick, setTick] = useState(0);
  const { data } = useApi(() => api.hostMemories(caseId), [caseId, tick], { keepPrevious: true });
  const [text, setText] = useState("");
  const [kind, setKind] = useState<(typeof KINDS)[number]>("theme");
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      setTick((t) => t + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Card data-testid="host-memory">
      <CardHeader>
        <CardTitle className="text-sm">Host memory</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 p-3 text-xs">
        {(data ?? []).length === 0 && <p className="text-muted-foreground">No memories yet.</p>}
        <ul className="space-y-1.5">
          {(data ?? []).map((m) => (
            <li key={m.id} className="rounded-md border border-border px-2.5 py-1.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="outline">{m.ref}</Badge>
                <Badge variant="outline">{m.kind.replace("_", " ")}</Badge>
                <Badge variant={m.origin === "editor" ? "info" : "default"}>{m.origin.replace("_", " ")}</Badge>
                {!m.active && <Badge variant="warning">inactive</Badge>}
                {m.used_by.length > 0 && (
                  <span className="text-muted-foreground">
                    used by {m.used_by.map((u) => `plan ${u.plan_id} ${u.segment_id}`).join(", ")}
                  </span>
                )}
              </div>
              {editing?.id === m.id ? (
                <div className="mt-1 flex gap-1.5">
                  <input
                    className="w-full rounded-md border border-border bg-background px-2 py-1"
                    value={editing.text}
                    onChange={(e) => setEditing({ id: m.id, text: e.target.value })}
                    aria-label="Memory text"
                  />
                  <Button
                    size="sm"
                    onClick={() => act(async () => { await api.updateHostMemory(m.id, { text: editing.text }); setEditing(null); })}
                  >
                    Save
                  </Button>
                </div>
              ) : (
                <p dir="auto" className="mt-1">{m.text}</p>
              )}
              <div className="mt-1 flex gap-1.5">
                <Button size="sm" variant="ghost" onClick={() => setEditing({ id: m.id, text: m.text })}>
                  Edit
                </Button>
                <Button size="sm" variant="ghost" onClick={() => act(() => api.updateHostMemory(m.id, { active: !m.active }))}>
                  {m.active ? "Deactivate" : "Activate"}
                </Button>
                {m.origin === "editor" && (
                  <Button size="sm" variant="ghost" onClick={() => act(() => api.deleteHostMemory(m.id))}>
                    Delete
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-1.5">
          <select
            className="rounded-md border border-border bg-background px-2 py-1"
            value={kind}
            onChange={(e) => setKind(e.target.value as (typeof KINDS)[number])}
            aria-label="Memory kind"
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k.replace("_", " ")}
              </option>
            ))}
          </select>
          <input
            className="min-w-48 flex-1 rounded-md border border-border bg-background px-2 py-1"
            placeholder="Add a memory the host should keep"
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="New memory text"
          />
          <Button
            size="sm"
            disabled={text.trim().length < 3}
            onClick={() => act(async () => { await api.addHostMemory({ case_id: caseId, kind, text }); setText(""); })}
          >
            Add memory
          </Button>
        </div>
        {error && <p className="text-rose-600 dark:text-rose-400">{error}</p>}
      </CardContent>
    </Card>
  );
}
