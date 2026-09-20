import type { ReactNode } from "react";
import { Link } from "react-router-dom";

interface Props {
  title: string;
  to: string;
  subtitle?: string;
  hint?: string;
  children: ReactNode;
}

// Clickable card that navigates to its target page. A real link (not a
// div with a click handler) so keyboard, screen readers, middle-click
// and context menus all work natively. Individual rows stay
// non-interactive so the card never nests buttons inside links.
export function WidgetCard({ title, to, subtitle, hint, children }: Props) {
  return (
    <Link className="widget-card" to={to} aria-label={`${title} — open`}>
      <header className="widget-head">
        <div className="widget-titles">
          <h2 className="widget-title">{title}</h2>
          {subtitle && <span className="widget-sub muted">{subtitle}</span>}
        </div>
        <span className="widget-go" aria-hidden="true">
          →
        </span>
      </header>
      <div className="widget-body">{children}</div>
      {hint && <footer className="widget-foot muted">{hint}</footer>}
    </Link>
  );
}
