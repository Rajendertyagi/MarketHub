// Single source of truth for the top-navigation. To add a link, append to a
// group's `items`; to add a whole group, append a `NavEntry`. The AppShell and
// StatusBar both derive from this — no other file hardcodes nav entries.

export interface NavItem {
  to: string;
  label: string;
}

export type NavEntry =
  | { kind: "links"; items: NavItem[] }
  | { kind: "dropdown"; label: string; items: NavItem[] };

export const NAV: NavEntry[] = [
  {
    kind: "links",
    items: [
      { to: "/dashboard", label: "Dashboard" },
      { to: "/watchlists", label: "Watchlists" },
      { to: "/alerts", label: "Alerts" },
    ],
  },
  {
    kind: "dropdown",
    label: "Markets",
    items: [
      { to: "/market-map", label: "Heatmap" },
      { to: "/breadth", label: "Breadth" },
      { to: "/sector-heatmap", label: "Sector Analysis" },
      { to: "/instruments", label: "Instruments" },
    ],
  },
  {
    kind: "links",
    items: [
      { to: "/charts", label: "Charts" },
      { to: "/scanners", label: "Scanners" },
      { to: "/fno", label: "F&O" },
      { to: "/option-chain", label: "Option Chain" },
    ],
  },
  {
    kind: "links",
    items: [
      { to: "/news", label: "News" },
      { to: "/sentiment", label: "Sentiment" },
      { to: "/ai-alerts", label: "AI Alerts" },
    ],
  },
  {
    kind: "dropdown",
    label: "More",
    items: [
      { to: "/chat", label: "Chat" },
      { to: "/subscriptions", label: "Subscriptions" },
      { to: "/diagnostics", label: "Test Center" },
      { to: "/settings", label: "Settings" },
    ],
  },
];

// Resolve the current view's display name from a pathname. Exact match first,
// then longest-prefix match (so e.g. /fno/... still resolves to "F&O").
export function labelForPath(pathname: string): string | undefined {
  const flat = NAV.flatMap((e) => e.items);
  const exact = flat.find((i) => i.to === pathname);
  if (exact) return exact.label;
  let best: NavItem | undefined;
  for (const it of flat) {
    if (pathname.startsWith(it.to) && (!best || it.to.length > best.to.length)) {
      best = it;
    }
  }
  return best?.label;
}
