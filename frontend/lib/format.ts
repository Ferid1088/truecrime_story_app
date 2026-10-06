export const LANGUAGES: Record<string, string> = {
  en: "English",
  de: "German",
  fa: "Persian",
  ar: "Arabic",
  unknown: "Unknown",
};

export const RTL_LANGS = new Set(["fa", "ar", "he", "ur"]);

export function isRtl(lang: string | null | undefined): boolean {
  return !!lang && RTL_LANGS.has(lang.toLowerCase());
}

export function langLabel(code: string | null | undefined): string {
  if (!code) return "—";
  return LANGUAGES[code] ?? code.toUpperCase();
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function estimateMinutes(words: number): number {
  return Math.max(1, Math.round(words / 135));
}

export function confidenceLabel(c: number): "high" | "medium" | "low" {
  if (c >= 0.75) return "high";
  if (c >= 0.45) return "medium";
  return "low";
}

export interface NarrativePlanSummary {
  title: string | null;
  central_question: string | null;
}

/**
 * `narrative_angle` may hold a serialized director plan (JSON) or plain prose.
 * Returns structured fields when parseable, otherwise null — callers should
 * render the raw string as-is in that case.
 */
export function parseNarrativePlan(raw: string | null | undefined): NarrativePlanSummary | null {
  if (!raw) return null;
  const t = raw.trim();
  if (!t.startsWith("{")) return null;
  try {
    const obj = JSON.parse(t) as Record<string, unknown>;
    const title = typeof obj.title === "string" ? obj.title : null;
    const cq = typeof obj.central_question === "string" ? obj.central_question : null;
    if (!title && !cq) return null;
    return { title, central_question: cq };
  } catch {
    return null;
  }
}
