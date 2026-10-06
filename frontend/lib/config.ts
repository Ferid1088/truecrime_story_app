/**
 * Single source of truth for the backend base URL.
 *
 * NEXT_PUBLIC_API_BASE_URL is the canonical variable; NEXT_PUBLIC_API_URL is
 * accepted as a legacy fallback. Both are inlined at build time, so they work
 * identically for browser- and server-side code.
 */
export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000"
).replace(/\/$/, "");
