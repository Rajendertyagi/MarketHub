import type { ReactNode } from "react";
import type { ApiError } from "@/types";

export function Button({
  children,
  variant = "default",
  ...rest
}: {
  children: ReactNode;
  variant?: "default" | "primary";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const cls = variant === "primary" ? "btn btn-primary" : "btn";
  return (
    <button className={cls} {...rest}>
      {children}
    </button>
  );
}

export function Select({
  className,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={className ?? "select"} {...rest} />;
}

export function Input({
  className,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={className ?? "input"} {...rest} />;
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { value: T; label: string }[];
  active: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <div
          key={t.value}
          role="tab"
          aria-selected={t.value === active}
          className={t.value === active ? "tab active" : "tab"}
          onClick={() => onChange(t.value)}
        >
          {t.label}
        </div>
      ))}
    </div>
  );
}

// Standard loading / empty / error presentation driven by an ApiError.
export function AsyncStateView({
  status,
  error,
  loadingLabel = "Loading…",
  emptyLabel = "No data.",
  onRetry,
}: {
  status: "loading" | "error" | "empty";
  error?: ApiError;
  loadingLabel?: string;
  emptyLabel?: string;
  onRetry?: () => void;
}) {
  if (status === "loading") {
    return (
      <div className="state">
        <div className="spinner" />
        <span className="muted">{loadingLabel}</span>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="state">
        <h3>Could not load data</h3>
        <p className="hint err">{error?.message ?? "Unknown error"}</p>
        {onRetry && (
          <Button variant="primary" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    );
  }
  return (
    <div className="state">
      <span className="muted">{emptyLabel}</span>
    </div>
  );
}
