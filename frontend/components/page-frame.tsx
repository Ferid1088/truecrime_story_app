"use client";

import { usePathname } from "next/navigation";

/** Pages that work on a lot of material at once (the picture review) use the whole width of the screen. */
const WIDE_PAGES = [/^\/documentary\/\d+\/review$/];

/** Keep the app content anchored to the main column with a readable width on ultra-wide displays. */
export function PageFrame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const wide = WIDE_PAGES.some((page) => page.test(path));
  return <div className={`w-full px-4 py-4 sm:px-6 sm:py-6 ${wide ? "max-w-none" : "max-w-[150rem]"}`}>{children}</div>;
}
