"use client";

import { usePathname } from "next/navigation";

/** Pages that work on a lot of material at once (the picture review) use the whole width of the screen. */
const WIDE_PAGES = [/^\/documentary\/\d+\/review$/];

/** Every other page is one column of a fixed greatest width (1600 px), centred next to the sidebar: rows end at its
 * border however wide the screen is, and a narrower screen still fills the width it has. */
export function PageFrame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const wide = WIDE_PAGES.some((page) => page.test(path));
  return <div className={`mx-auto px-4 py-4 sm:px-6 sm:py-6 ${wide ? "max-w-none" : "max-w-[100rem]"}`}>{children}</div>;
}
