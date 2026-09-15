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
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const navRef = useRef<HTMLElement>(null);

  // Close any open dropdown on navigation or outside click.
  useEffect(() => setOpenIdx(null), [location.pathname]);
  useEffect(() => {
    if (openIdx === null) return;
    const onDown = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) {
        setOpenIdx(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [openIdx]);

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <div className="app-brand">
          <NavLink to="/dashboard" className="app-home" aria-label="Home" title="Home">
            <Icon name="home" />
          </NavLink>
        </div>

        <nav className="app-nav" aria-label="Primary" ref={navRef}>
          {NAV.map((seg, i) => (
            <div className="nav-segment" key={i}>
              {seg.kind === "links" ? (
                seg.items.map((it) => (
                  <NavLink
                    key={it.to}
                    to={it.to}
                    className={({ isActive }) =>
                      isActive ? "nav-link active" : "nav-link"
                    }
                  >
                    {it.label}
                  </NavLink>
                ))
              ) : (
                <div className="nav-dropdown">
                  <button
                    type="button"
                    className={`nav-dropdown-trigger${openIdx === i ? " open" : ""}`}
                    aria-expanded={openIdx === i}
                    aria-haspopup="true"
                    onClick={() => setOpenIdx(openIdx === i ? null : i)}
                  >
                    {seg.label}
                    <Icon name="chevron-down" size={14} />
                  </button>
                  {openIdx === i ? (
                    <div className="nav-dropdown-menu" role="menu">
                      {seg.items.map((it) => (
                        <NavLink
                          key={it.to}
                          to={it.to}
                          role="menuitem"
                          className="nav-dropdown-item"
                          onClick={() => setOpenIdx(null)}
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
