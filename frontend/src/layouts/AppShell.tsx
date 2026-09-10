import { NavLink, Outlet } from "react-router-dom";
import { useTheme } from "@/app/ThemeProvider";

const NAV = [
  { to: "/charts", label: "Charts" },
  { to: "/scanners", label: "Scanners" },
  { to: "/fno", label: "F&O Workspace" },
  { to: "/subscriptions", label: "Subscriptions" },
  { to: "/instruments", label: "Instruments" },
  { to: "/news", label: "News" },
  { to: "/alerts", label: "Alerts" },
  { to: "/ai-alerts", label: "AI Alerts" },
  { to: "/mcp", label: "MCP Tools" },
  { to: "/logs", label: "Logs" },
  { to: "/diagnostics", label: "Test Center" },
  { to: "/market-map", label: "Market Map" },
  { to: "/breadth", label: "Breadth" },
  { to: "/sector-heatmap", label: "Sector Heatmap" },
];

export function AppShell() {
  const { theme, toggleTheme } = useTheme();
  return (
    <div className="app-shell">
      <div className="app-brand">MarketHub</div>
      <div className="app-topbar">
        <button
          className="btn"
          onClick={toggleTheme}
          aria-label="Toggle theme"
          title="Toggle dark/light"
        >
          {theme === "dark" ? "☾ Dark" : "☀ Light"}
        </button>
      </div>
      <nav className="app-nav">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              isActive ? "nav-link active" : "nav-link"
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
