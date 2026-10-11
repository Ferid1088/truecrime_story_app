"use client";

import { ThemeProvider } from "next-themes";
import { AuthGate } from "@/components/auth-gate";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
      <AuthGate>{children}</AuthGate>
    </ThemeProvider>
  );
}
