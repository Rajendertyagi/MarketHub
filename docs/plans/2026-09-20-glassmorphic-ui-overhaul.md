# Glassmorphic UI Overhaul Plan

## Current State Analysis

**File count:** 25 CSS files, ~3,500 lines total
**Structure:** 4-layer cascade (`@layer tokens` → `base` → `layout` → `components` → `features` → `utilities`)
**Current aesthetic:** Flat surfaces with hard borders (`border: 1px solid var(--border)`), compact spacing (8-16px), minimal shadows
**Theme system:** OKLCH colors with dark/light modes via `[data-theme]` attribute

---

## Design Token Overhaul

###tokens.css
Update root and theme variables:

```css
:root {
  --space-1: 6px;      /* was 4px */
  --space-2: 10px;     /* was 8px */
  --space-3: 16px;     /* was 12px */
  --space-4: 24px;     /* was 16px */
  --space-5: 32px;     /* was 24px */
  --space-6: 48px;     /* was 32px */

  --radius-sm: 6px;
  --radius-md: 10px;   /* was 4px */
  --radius-lg: 16px;   /* was 6px */

  --font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
```

### Dark theme (glassmorphic)
```css
[data-theme="dark"] {
  --bg: oklch(0.12 0 0);           /* was 0% */
  --surface-1: rgba(255, 255, 255, 0.04);  /* glass */
  --surface-1-blur: blur(12px);    /* new */
  --surface-2: rgba(255, 255, 255, 0.06);
  --surface-3: rgba(255, 255, 255, 0.08);
  --text: oklch(0.94 0.01 100);
  --text-muted: oklch(0.72 0.008 100);
  --text-faint: oklch(0.52 0.006 80);

  --shadow-card: 0 8px 32px rgba(0, 0, 0, 0.2), 0 0 0 1px rgba(255, 255, 255, 0.05);
  --shadow-float: 0 16px 48px rgba(0, 0, 0, 0.3), 0 0 0 1px rgba(255, 255, 255, 0.08);
  --edge: transparent;             /* no border line */
}
```

### Light theme (glassmorphic)
```css
[data-theme="light"] {
  --bg: oklch(0.96 0.012 250);
  --surface-1: rgba(255, 255, 255, 0.7);
  --surface-1-blur: blur(16px);
  --surface-2: rgba(255, 255, 255, 0.8);
  --surface-3: rgba(255, 255, 255, 0.9);
  --text: oklch(0.25 0.01 250);
  --text-muted: oklch(0.45 0.01 250);
  --text-faint: oklch(0.62 0.01 250);

  --shadow-card: 0 8px 32px rgba(0, 0, 0, 0.1), 0 0 0 1px rgba(0, 0, 0, 0.05);
  --shadow-float: 0 16px 48px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.08);
  --edge: transparent;
}
```

---

## CSS Refactor Priority

### Phase 1: Core System Files (High Impact)
**Files:** `tokens.css`, `base.css`, `layout.css`, `components.css`

#### Base layer changes
- Add glass backdrop filter foundation
- Update font size hierarchy (+1px across board)
- Increase padding defaults (4px → 6px, 8px → 10px)

#### Layout layer changes
- `.app-topbar`: Add `backdrop-filter: blur(12px)` + glass background
- `.app-main`: Increase padding (12px → 20px)
- `.nav-link`: Add hover glass effect
- `.nav-dropdown-menu`: Glass backdrop + floating shadow
- `.icon-btn`: Remove border, add glass surface

#### Components layer changes
- `.btn`: Remove `border: 1px solid`, add glass surface
- `.input, .select`: Remove border, add glass background
- `.card`: Add `backdrop-filter: blur(12px)` + glass surface
- `.panel`: Same treatment as card
- `.table td/th`: Replace `border-bottom: 1px solid` with layering
- `.tabs`: Remove bottom border, use active indicator only
- `.chip`: Remove border, use filled glass variant
- `.modal-backdrop`: Increase blur (4px → 8px)
- `.modal-card`: Glass surface + larger shadow

---

### Phase 2: Feature Files (Medium Impact)
**Files:** All 13 feature CSS files in `styles/features/`

#### Dashboard (`dashboard.css`)
- `.app-ticker`: Remove border-bottom, use surface layering
- `.ticker-item`: Remove border-right divider
- `.stale-tag`: Change to filled pill (no border)
- `.movers-tab`: Glass-filled variant
- `.widget-card`: Remove border, add hover lift effect
- `.idx-card`: Add glass surface + larger shadow on hover

#### News (`news.css`)
- `.news-pane`: Glass surface + backdrop filter
- `.news-pane-header`: Remove border-bottom
- `.news-article-row`: Remove border-bottom, use hover glass
- `.news-item`: Glass surface
- Sentiment chips: Filled variants only (no bordered)

#### Settings (`settings.css`)
- Forms: Input fields get glass treatment
- Broker cards: Glass surface
- Auth panels: Glass elevation

---

### Phase 3: Minor Features (Low Impact)
**Files:** Remaining 11 feature CSS files

Process same as Phase 2 — remove borders, add glass surfaces.

---

## Implementation Checklist

### Step 1: Update `tokens.css` (~15 min)
- [ ] Add `--radius-*` increments
- [ ] Add `--space-*` increments
- [ ] Add `--surface-*-blur` for backdrop filters
- [ ] Update dark theme with glass values
- [ ] Update light theme with glass values
- [ ] Keep OKLCH color palette (no changes needed)

### Step 2: Update `base.css` (~10 min)
- [ ] Add `font-family: 'Inter'` import in index.css (Google Fonts or system fallback)
- [ ] Increase default padding/margin scale

### Step 3: Update `layout.css` (~20 min)
- [ ] Add `backdrop-filter` to `.app-topbar`
- [ ] Add glass background to `.nav-dropdown-menu`
- [ ] Remove borders from `.icon-btn`
- [ ] Update `.app-main` padding

### Step 4: Update `components.css` (~30 min)
- [ ] Refactor `.btn` — glass surface, no border
- [ ] Refactor `.input/.select` — glass background
- [ ] Refactor `.card/.panel` — glass surface + larger shadows
- [ ] Refactor tables — remove borders, use layering
- [ ] Refactor tabs — remove border-bottom
- [ ] Refactor chips — filled glass style
- [ ] Refactor modals — glass backdrop + card

### Step 5: Update feature CSS files (~60 min)
- [ ] `dashboard.css` — ticker, cards, widgets
- [ ] `news.css` — panes, articles, items
- [ ] `settings.css` — forms, broker cards
- [ ] `alerts.css` — alert cards
- [ ] `watchlists.css` — watchlist items
- [ ] `charts.css` — chart containers
- [ ] `sentiment.css` — sentiment items
- [ ] Remaining 7 files — systematic border removal + glass addition

### Step 6: Final polish (~20 min)
- [ ] Verify both dark and light themes
- [ ] Check hover/active states
- [ ] Ensure accessibility (contrast ratios)
- [ ] Mobile responsiveness check

---

## Design Principles (from user spec)

| Principle | Implementation |
|-----------|---------------|
| **Borderless** | Remove all `border: 1px solid`; use `background` layering instead |
| **Floating surfaces** | `backdrop-filter: blur(12px)` + `rgba()` backgrounds |
| **Subtle elevation** | Dual-shadow: `0 8px 32px rgba(0,0,0,0.2), 0 0 0 1px rgba(255,255,255,0.05)` |
| **Airy layout** | Increase space scale (+50%), padding (+8px base) |
| **Smooth transitions** | `transition: 0.2s ease` on all interactive elements |
| **Clean typography** | Add 'Inter' font family, clear hierarchy |
| **No quick fixes** | Never add borders for visibility — use surface shading |

---

## Risks & Mitigations

1. **Browser compatibility**: `backdrop-filter` not supported in IE, Firefox needs `-webkit-` prefix
   - Mitigation: Use vendor prefixes + graceful degradation (solid background fallback)

2. **Performance**: Multiple blur filters can impact rendering
   - Mitigation: Limit blur to 2-3 elements max; use `will-change: transform` sparingly

3. **Light theme readability**: Glass on light backgrounds can be subtle
   - Mitigation: Increase surface opacity slightly in light mode

4. **Existing tests**: No CSS tests exist, but React component tests should pass
   - Mitigation: Run existing test suite after each phase

---

## Estimated Time

| Phase | Files | Est. Time |
|-------|-------|-----------|
| 1. Core system | 4 files | 75 min |
| 2. Feature files | 8 major files | 60 min |
| 3. Minor features | 11 files | 45 min |
| 4. Polish & verify | All | 20 min |
| **Total** | **25 files** | **~4 hours** |

---

## Next Steps

1. Confirm this plan aligns with your vision
2. Pick a starting point (Phase 1 vs. Phase 2 first)
3. I'll implement iteratively, testing after each phase
