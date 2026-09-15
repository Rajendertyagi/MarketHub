import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

interface Props {
  title: string;
  to: string;
  subtitle?: string;
  hint?: string;
  children: ReactNode;
}

// Clickable, keyboard-accessible card that navigates to its target page. The
// whole surface is the affordance; individual rows stay non-interactive so the
// card never nests buttons inside buttons.
export function WidgetCard({ title, to, subtitle, hint, children }: Props) {
  const navigate = useNavigate();
  return (
    <section
      className="widget-card"
      role="link"
      tabIndex={0}
      aria-label={`${title} — open`}
      onClick={() => navigate(to)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          navigate(to);
        }
      }}
    >
      <header className="widget-head">
        <div className="widget-titles">
          <h2 className="widget-title">{title}</h2>
          {subtitle && <span className="widget-sub muted">{subtitle}</span>}
        </div>
        <span className="widget-go" aria-hidden="true">→</span>
      </header>
      <div className="widget-body">{children}</div>
      {hint && <footer className="widget-foot muted">{hint}</footer>}
    </section>
  );
}
