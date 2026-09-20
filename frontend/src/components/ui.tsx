import type { ReactNode } from "react";
import { cloneElement, isValidElement, useEffect, useId, useRef } from "react";
import type { ApiError } from "@/types";

export function Button({
  children,
  variant = "default",
  type = "button",
  ...rest
}: {
  children: ReactNode;
  variant?: "default" | "primary";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const cls = variant === "primary" ? "btn btn-primary" : "btn";
  return (
    <button className={cls} type={type} {...rest}>
      {children}
    </button>
  );
}

export function Select({ className, ...rest }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={className ?? "select"} {...rest} />;
}

export function Input({ className, ...rest }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={className ?? "input"} {...rest} />;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  // Associate the label with its control via a generated id so
  // screen readers announce every field correctly.
  const id = useId();
  const control = isValidElement<{ id?: string }>(children)
    ? cloneElement(children, { id: children.props.id ?? id })
    : children;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {control}
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
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={t.value === active}
          className={t.value === active ? "tab active" : "tab"}
          onClick={() => onChange(t.value)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// Accessible modal primitive backed by the native <dialog> element.
//
// Native behavior where supported: top layer, Escape-to-close, initial
// focus. Fallback (older engines / test DOM without showModal): a plain
// open dialog with manual Escape handling. Backdrop clicks dismiss in
// both modes; keyboard users always have Escape plus the inner Close
// control, so the backdrop click is pointer-only convenience.
export function Modal({
  label,
  onClose,
  children,
}: {
  label: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (typeof dlg.showModal === "function") {
      if (!dlg.open) dlg.showModal();
      return () => {
        if (dlg.open) dlg.close();
      };
    }
    // Fallback (older engines / test DOM without showModal): plain open
    // dialog; Escape bubbles from any focused child to the dialog handler.
    dlg.setAttribute("open", "");
    dlg.focus();
    return () => {
      dlg.removeAttribute("open");
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className="modal-dialog"
      aria-label={label}
      tabIndex={-1}
      onClose={() => onCloseRef.current()}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCloseRef.current();
      }}
      onKeyDown={(e) => {
        // Dismiss on Escape. Native in showModal mode (plus onClose);
        // primary path in fallback mode where cancel events don't exist.
        if (e.key === "Escape") onCloseRef.current();
      }}
    >
      {children}
    </dialog>
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
