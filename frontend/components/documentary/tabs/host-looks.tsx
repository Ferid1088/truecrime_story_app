"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const CAMERAS = ["CAM01_FRONT", "CAM02_LEFT_45", "CAM03_RIGHT_45", "CAM04_LEFT_SIDE", "CAM05_RIGHT_SIDE", "CAM06_WIDE"];
const FRAMINGS = ["WIDE", "MEDIUM", "MEDIUM_CLOSE", "CLOSE"];

/** The host's looks (picture in the studio: camera, framing, outfit, pose): request
 *  one at a time, approve before HeyGen sees it; and his visual memory. */
export function HostLooksPanel({ language, channel }: { language: string; channel?: string }) {
  const [tick, setTick] = useState(0);
  const { data: looks } = useApi(() => api.hostLooks(language), [language, tick], { keepPrevious: true });
  const { data: appearances } = useApi(() => api.hostAppearances(channel), [channel, tick], { keepPrevious: true });
  const [camera, setCamera] = useState(CAMERAS[0]);
  const [framing, setFraming] = useState(FRAMINGS[0]);
  const [outfit, setOutfit] = useState("");
  const [busy, setBusy] = useState<number | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(id: number | "new", fn: () => Promise<unknown>) {
    setBusy(id);
    setError(null);
    try {
      await fn();
      setTick((t) => t + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card data-testid="host-looks">
      <CardHeader>
        <CardTitle className="text-sm">Host looks and visual memory</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 p-3 text-xs">
        <p className="text-muted-foreground">
          A look is a picture of the host inside the studio. Only approved looks can be chosen by the stage
          director, and nothing reaches HeyGen before you approve it. Look pictures are generated one at a time.
        </p>
        <ul className="space-y-1.5">
          {(looks ?? []).map((l) => (
            <li key={l.id} className="flex flex-wrap items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5">
              <Badge variant="outline">{l.camera}</Badge>
              <Badge variant="outline">{l.framing}</Badge>
              <span>{l.outfit_id}</span>
              <Badge variant={l.status === "approved" ? "success" : l.status === "rejected" ? "danger" : "warning"}>
                {l.status}
              </Badge>
              {l.heygen_look_id && <Badge variant="info">HeyGen look</Badge>}
              {l.status === "requested" && (
                <Button size="sm" variant="secondary" loading={busy === l.id} onClick={() => act(l.id, () => api.generateHostLook(l.id))}>
                  Generate picture
                </Button>
              )}
              {l.status === "generated" && (
                <>
                  <Button size="sm" loading={busy === l.id} onClick={() => act(l.id, () => api.approveHostLook(l.id))}>
                    Approve
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => act(l.id, () => api.rejectHostLook(l.id, "not right"))}>
                    Reject
                  </Button>
                </>
              )}
              {l.status === "approved" && !l.heygen_look_id && (
                <Button size="sm" variant="secondary" loading={busy === l.id} onClick={() => act(l.id, () => api.uploadHostLook(l.id))}>
                  Upload to HeyGen
                </Button>
              )}
              {l.status === "rejected" && (
                <Button size="sm" variant="ghost" onClick={() => act(l.id, () => api.regenerateHostLook(l.id, "keep the studio exactly as in the reference"))}>
                  Regenerate
                </Button>
              )}
            </li>
          ))}
          {(looks ?? []).length === 0 && <li className="text-muted-foreground">No looks yet.</li>}
        </ul>
        <div className="flex flex-wrap gap-1.5">
          <select className="rounded-md border border-border bg-background px-2 py-1" value={camera} onChange={(e) => setCamera(e.target.value)} aria-label="Camera">
            {CAMERAS.map((c) => <option key={c}>{c}</option>)}
          </select>
          <select className="rounded-md border border-border bg-background px-2 py-1" value={framing} onChange={(e) => setFraming(e.target.value)} aria-label="Framing">
            {FRAMINGS.map((f) => <option key={f}>{f}</option>)}
          </select>
          <input className="min-w-40 rounded-md border border-border bg-background px-2 py-1" placeholder="outfit id" value={outfit} onChange={(e) => setOutfit(e.target.value)} aria-label="Outfit id" />
          <Button
            size="sm"
            disabled={!outfit.trim()}
            loading={busy === "new"}
            onClick={() => act("new", () => api.requestHostLook({ language, camera, framing, outfit_id: outfit.trim(), pose: "P01_NEUTRAL_SEATED" }))}
          >
            Request look
          </Button>
        </div>
        <div>
          <p className="mb-1 font-medium">Recent appearances{channel ? ` (${channel})` : ""}</p>
          <ul className="space-y-1 text-muted-foreground">
            {(appearances ?? []).slice(0, 10).map((a) => (
              <li key={a.id}>
                {a.outfit_id} · {a.posture} · {a.lighting} · {a.shots.map((s) => `${s.camera}/${s.framing}`).join(" → ")} ·{" "}
                {a.emotion}
              </li>
            ))}
            {(appearances ?? []).length === 0 && <li>None yet.</li>}
          </ul>
        </div>
        {error && <p className="text-rose-600 dark:text-rose-400">{error}</p>}
      </CardContent>
    </Card>
  );
}
