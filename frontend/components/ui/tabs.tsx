"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/** id of the panel a TabBar controls — give it to the element rendering the active tab. */
export function tabPanelId(idPrefix: string) {
  return `${idPrefix}-panel`;
}

export function tabId(idPrefix: string, index: number) {
  return `${idPrefix}-tab-${index}`;
}

/**
 * WAI-ARIA tab list: one tab stop, Arrow/Home/End move between tabs.
 * Pair with an element that has role="tabpanel", id={tabPanelId(idPrefix)}
 * and aria-labelledby={tabId(idPrefix, activeIndex)}.
 */
export function TabBar<T extends string>({
  tabs,
  value,
  onChange,
  idPrefix,
  label,
}: {
  tabs: readonly T[];
  value: T;
  onChange: (tab: T) => void;
  idPrefix: string;
  label: string;
}) {
  const refs = React.useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(e: React.KeyboardEvent, index: number) {
    const last = tabs.length - 1;
    const next =
      e.key === "ArrowRight"
        ? index === last ? 0 : index + 1
        : e.key === "ArrowLeft"
          ? index === 0 ? last : index - 1
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? last
              : null;
    if (next == null) return;
    e.preventDefault();
    onChange(tabs[next]);
    refs.current[next]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label={label}
      className="mb-5 flex gap-1 overflow-x-auto border-b border-border"
    >
      {tabs.map((t, i) => (
        <button
          key={t}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="tab"
          id={tabId(idPrefix, i)}
          aria-selected={value === t}
          aria-controls={tabPanelId(idPrefix)}
          tabIndex={value === t ? 0 : -1}
          onClick={() => onChange(t)}
          onKeyDown={(e) => onKeyDown(e, i)}
          className={cn(
            "-mb-px cursor-pointer whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
            value === t
              ? "border-primary font-medium text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {t}
        </button>
      ))}
    </div>
  );
}
