"use client";

import { useState } from "react";
import { ApiError, autopilotApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** Adds one PNG photo to an outfit of a language: a new name makes a new outfit. The photo's pose name must start
 * with sit or stand (for example sitHands, stand). It stays in the outfit library for every later film. */
export function AddOutfitPhoto({
  language,
  outfitNames,
  onAdded,
}: {
  language: string;
  outfitNames: string[];
  onAdded: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [pose, setPose] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listId = `outfit-names-${language}`;

  async function add() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await autopilotApi.addOutfitPhoto(language, name, pose, file);
      setName("");
      setPose("");
      setFile(null);
      setOpen(false);
      onAdded();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        className="mt-1 text-xs text-primary underline"
        onClick={() => setOpen(true)}
        data-testid={`add-outfit-open-${language}`}
      >
        Add an outfit photo
      </button>
    );
  }
  return (
    <div className="mt-2 space-y-2 rounded-md border border-border p-2">
      <p className="text-xs text-muted-foreground">
        A new outfit name makes a new outfit; an existing name adds another photo to it. Pose names start with sit or
        stand, for example sitHands or standPockets.
      </p>
      <Input list={listId} value={name} onChange={(e) => setName(e.target.value)} placeholder="Outfit name"
        data-testid={`add-outfit-name-${language}`} />
      <datalist id={listId}>{outfitNames.map((n) => <option key={n} value={n} />)}</datalist>
      <Input value={pose} onChange={(e) => setPose(e.target.value)} placeholder="Pose name, e.g. sitHands"
        data-testid={`add-outfit-pose-${language}`} />
      <input type="file" accept="image/png" className="block text-xs"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)} data-testid={`add-outfit-file-${language}`} />
      {error && <p className="text-xs text-rose-600">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" disabled={busy || !file || !name.trim() || !pose.trim()} onClick={add}
          data-testid={`add-outfit-save-${language}`}>
          {busy ? "Adding…" : "Add photo"}
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </div>
  );
}
