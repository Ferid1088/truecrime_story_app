"use client";

import { useState } from "react";
import { Radio } from "lucide-react";
import { ApiError, autopilotApi, type ChannelSettingRow } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { humanize, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

/** One placeholder per language and platform. A handle is a public name; no password or token goes here. */
export function ChannelsCard() {
  const { data, refetch } = useApi(() => autopilotApi.channels(), [], { keepPrevious: true });
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  if (!data) return null;
  const key = (c: ChannelSettingRow) => `${c.language}/${c.platform}`;

  async function save(c: ChannelSettingRow) {
    setMessage(null);
    try {
      await autopilotApi.setHandle(c.language, c.platform, drafts[key(c)] ?? c.handle);
      setMessage({ ok: true, text: `Saved ${langLabel(c.language)} / ${humanize(c.platform)}.` });
      refetch();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : "Could not save." });
    }
  }

  return (
    <Card className="mt-4" data-testid="channel-settings">
      <CardContent className="space-y-3 pt-4">
        <div className="flex items-center gap-2">
          <Radio className="size-4" aria-hidden />
          <h2 className="text-sm font-semibold">Channels</h2>
        </div>
        <p className="text-xs text-muted-foreground">
          The channel each language posts to, per platform. Public handle only: never a password or token. Nothing is
          connected yet, so publishing stays a dry run.
        </p>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {data.channels.map((c) => (
            <div key={key(c)} className="flex items-center gap-2 text-xs">
              <span className="w-40 shrink-0">
                {langLabel(c.language)} · {humanize(c.platform)}
              </span>
              <Input
                aria-label={`Handle for ${langLabel(c.language)} ${humanize(c.platform)}`}
                value={drafts[key(c)] ?? c.handle}
                maxLength={100}
                placeholder="@handle"
                onChange={(e) => setDrafts((d) => ({ ...d, [key(c)]: e.target.value }))}
              />
              <Button size="sm" variant="outline" onClick={() => save(c)}>
                Save
              </Button>
              <Badge variant="outline">Not connected</Badge>
            </div>
          ))}
        </div>
        {message && (
          <p role="status" className={message.ok ? "text-xs text-emerald-600" : "text-xs text-rose-600"}>
            {message.text}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
