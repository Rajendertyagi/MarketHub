import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useTheme } from "@/app/ThemeProvider";
import { Icon } from "@/components/Icon";
import { NAV } from "./nav";
import { StatusBar } from "./StatusBar";

// Compact top navbar + bottom status strip. Navigation is data-driven from
// `./nav` so adding links/groups/dropdowns never touches this component.
export function AppShell() {
  const { theme, toggleTheme } = useTheme();
  const location = useLocation();
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const navRef = useRef<HTMLElement>(null);

  // Close any open dropdown on navigation: adjust state during render
  // (React's previous-render pattern), so no navigation effect is needed.
  const [lastPath, setLastPath] = useState(location.pathname);
  if (location.pathname !== lastPath) {
    setLastPath(location.pathname);
    setOpenMenu(null);
  }
  useEffect(() => {
    if (openMenu === null) return;
    const onDown = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) {
        setOpenMenu(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [openMenu]);

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <div className="app-brand">
          <NavLink to="/dashboard" className="app-home" aria-label="Home" title="Home">
            <Icon name="home" />
          </NavLink>
        </div>

        <nav className="app-nav" aria-label="Primary" ref={navRef}>
          {NAV.map((seg) => (
            <div
              className="nav-segment"
              key={
                seg.kind === "links" ? `links-${seg.items[0]?.to ?? "start"}` : `menu-${seg.label}`
              }
            >
              {seg.kind === "links" ? (
                seg.items.map((it) => (
                  <NavLink
                    key={it.to}
                    to={it.to}
                    className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
                  >
                    {it.label}
                  </NavLink>
                ))
              ) : (
                <div className="nav-dropdown">
                  <button
                    type="button"
                    className={`nav-dropdown-trigger${openMenu === seg.label ? " open" : ""}`}
                    aria-expanded={openMenu === seg.label}
                    aria-haspopup="true"
                    onClick={() => setOpenMenu(openMenu === seg.label ? null : seg.label)}
                  >
                    {seg.label}
                    <Icon name="chevron-down" size={14} />
                  </button>
                  {openMenu === seg.label ? (
                    <div className="nav-dropdown-menu" role="menu">
                      {seg.items.map((it) => (
                        <NavLink
                          key={it.to}
                          to={it.to}
                          role="menuitem"
                          className="nav-dropdown-item"
                          onClick={() => setOpenMenu(null)}
                        >
                          {it.label}
                        </NavLink>
                      ))}
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          ))}
        </nav>

        <button
          type="button"
          className="icon-btn theme-toggle"
          onClick={toggleTheme}
          aria-label="Toggle theme"
          title="Toggle theme"
        >
          <Icon name={theme === "dark" ? "sun" : "moon"} />
        </button>
      </header>

      <main className="app-main">
        <Outlet />
      </main>

      <StatusBar />
    </div>
  );
}
