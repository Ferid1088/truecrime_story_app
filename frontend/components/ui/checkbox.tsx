import * as React from "react";
import { cn } from "@/lib/utils";

interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
}

export function Checkbox({ className, label, id, ...props }: CheckboxProps) {
  const generatedId = React.useId();
  const inputId = id ?? generatedId;
  return (
    <label htmlFor={inputId} className="flex cursor-pointer items-center gap-2 text-sm select-none">
      <input
        id={inputId}
        type="checkbox"
        className={cn(
          "size-3.5 shrink-0 cursor-pointer appearance-none rounded border border-border-strong bg-transparent transition-colors",
          "checked:border-primary checked:bg-primary",
          "checked:bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2016%2016%22%20fill%3D%22none%22%3E%3Cpath%20d%3D%22M4%208l3%203%205-6%22%20stroke%3D%22white%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E')] checked:bg-center checked:bg-no-repeat",
          "focus-visible:ring-2 focus-visible:ring-ring/40 outline-none",
          className,
        )}
        {...props}
      />
      {label && <span className="text-foreground">{label}</span>}
    </label>
  );
}
