"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { authApi } from "@/lib/api";

/**
 * Asks the API once whether this browser has a valid session. With no TRUECRIME_ACCESS_TOKEN configured the API
 * answers "authenticated" and nothing changes; otherwise unauthenticated visitors are sent to /login.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    authApi
      .session()
      .then((s) => {
        if (cancelled) return;
        if (!s.authenticated && pathname !== "/login") router.replace("/login");
        else setReady(true);
      })
      // API unreachable: render the app so its own error states explain what is wrong.
      .catch(() => !cancelled && setReady(true));
    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  if (pathname === "/login") return <>{children}</>;
  return ready ? <>{children}</> : null;
}
