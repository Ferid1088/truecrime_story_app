import * as React from "react";
import { cn } from "@/lib/utils";

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "flex min-h-16 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm outline-none transition-colors",
        "placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:border-border-strong",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}
