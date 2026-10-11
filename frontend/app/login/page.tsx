"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, authApi } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authApi.login(token);
      router.replace("/");
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto mt-24 max-w-sm space-y-4">
      <h1 className="text-xl font-semibold">Sign in</h1>
      <p className="text-sm text-muted-foreground">Enter the access token configured for this server.</p>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm" htmlFor="token">
          Access token
        </label>
        <input
          id="token"
          type="password"
          autoComplete="current-password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm"
        />
        {error && (
          <p role="alert" className="text-sm text-rose-500">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy || !token}
          className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
