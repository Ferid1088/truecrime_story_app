"use client";

/** Every page uses the whole width next to the sidebar, so a wide screen is filled instead of leaving a narrow column
 * in an empty field; running text keeps its own reading width (max-w-2xl / max-w-3xl) where it is written. */
export function PageFrame({ children }: { children: React.ReactNode }) {
  return <div className="px-4 py-4 sm:px-6 sm:py-6 xl:px-10">{children}</div>;
}
