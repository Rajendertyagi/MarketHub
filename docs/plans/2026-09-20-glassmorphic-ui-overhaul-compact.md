# Glassmorphic UI Overhaul Plan — Compact Layout

## Design Adjustments from Previous Plan

| Aspect | Original Suggestion | Compact Override |
|--------|--------------------|------------------|
| Base font size | 14px | **13px** |
| Spacing scale | +50% (spacious) | **-30%** (compact) |
| Font family | Inter + system | **System fonts only** |
| Browser support | Standard | **-webkit-backdrop-filter added** |

---

## Updated Design Tokens

### Compact Spacing Scale (`tokens.css`)

```css
:root {
  /* Compact but breathable — 30% reduction vs. original 'spacious' suggestion */
  --space-1: 4px;    /* unchanged from current */
  --space-2: 8px;    /* unchanged from current */
  --space-3: 12px;   /* unchanged from current */
  --space-4: 16px;   /* was 24px in spacious, reduced for compact */
  --space-5: 20px;   /* was 32px, reduced */
  --space-6: 28px;   /* was 48px, reduced */

  /* Border radius — slightly larger than current for glass feel */
  --radius-sm: 6px;  /* was 3px */
  --radius-md: 8px;  /* was 4px */
  --radius-lg: 12px; /* was 6px */

  /* Typography */
  --font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-size-xs: 11px;
  --font-size-sm: 12px;
  --font-size-md: 13px;      /* base = 13px */
  --font-size-base: 13px;    /* was 14px */
  --font-size-lg: 15px;
  --font-size-xl: 17px;
  --font-size-2xl: 20px;

  /* Motion */
  --motion-fast: 150ms;
  --ease: cubic-bezier(0.4, 0, 0.2, 1);
}
```

### Dark Theme — Glassmorphic Compact

```css
[data-theme="dark"] {
  color-scheme: dark;
  
  /* Background — subtle off-black */
  --bg: oklch(0.13 0 0);
  
  /* Glass surfaces — translucent, layered */
  --surface-1: rgba(255, 255, 255, 0.04);
  --surface-2: rgba(255, 255, 255, 0.06);
  --surface-3: rgba(255, 255, 255, 0.08);
  --surface-glass-blur: blur(12px);
  
  /* Text — high contrast on dark glass */
  --text: oklch(0.95 0.01 100);
  --text-muted: oklch(0.70 0.008 100);
  --text-faint: oklch(0.50 0.006 80);
  
  /* Shadows — dual-layer for glass edge */
  --shadow-card: 0 8px 32px rgba(0, 0, 0, 0.25), 0 0 0 1px rgba(255, 255, 255, 0.05);
  --shadow-float: 0 12px 48px rgba(0, 0, 0, 0.35), 0 0 0 1px rgba(255, 255, 255, 0.08);
  --edge: transparent;
  
  /* Accent colors — unchanged from current OKLCH palette */
  --accent: oklch(66% 0.124 254);
  --accent-hover: oklch(72% 0.124 254);
  --accent-active: oklch(78% 0.124 254);
  --accent-soft: color-mix(in oklch, var(--accent) 50%, transparent);
  
  /* Financial colors — unchanged */
  --pos: oklch(78% 0.17 155);
  --neg: oklch(72% 0.18 25);
  --success: oklch(72% 0.14 150);
  --danger: oklch(70% 0.17 25);
  --warning: oklch(68% 0.13 75);
  --info: oklch(68% 0.12 240);
}
```

### Light Theme — Glassmorphic Compact

```css
[data-theme="light"] {
  color-scheme: light;
  
  /* Background — subtle warm white */
  --bg: oklch(0.97 0.012 250);
  
  /* Glass surfaces — frosted white, readable text on top */
  --surface-1: rgba(255, 255, 255, 0.75);
  --surface-2: rgba(255, 255, 255, 0.85);
  --surface-3: rgba(255, 255, 255, 0.95);
  --surface-glass-blur: blur(16px);
  
  /* Text — dark on light glass */
  --text: oklch(0.20 0.01 250);
  --text-muted: oklch(0.45 0.01 250);
  --text-faint: oklch(0.65 0.01 250);
  
  /* Shadows — softer for light mode */
  --shadow-card: 0 8px 32px rgba(0, 0, 0, 0.08), 0 0 0 1px rgba(0, 0, 0, 0.05);
  --shadow-float: 0 12px 48px rgba(0, 0, 0, 0.12), 0 0 0 1px rgba(0, 0, 0, 0.08);
  --edge: transparent;
  
  /* Accent colors — adjusted for light readability */
  --accent: oklch(52% 0.15 254);
  --accent-hover: oklch(47% 0.15 254);
  --accent-active: oklch(42% 0.15 254);
  --accent-soft: color-mix(in oklch, var(--accent) 50%, transparent);
  
  /* Financial colors — adjusted for light mode */
  --pos: oklch(45% 0.14 150);
  --neg: oklch(52% 0.17 25);
  --success: oklch(45% 0.14 150);
  --danger: oklch(52% 0.17 25);
  --warning: oklch(50% 0.13 75);
  --info: oklch(52% 0.12 240);
}
```

---

## Implementation Phases

### Phase 1: Core System (4 files)
**Preview on Dashboard Widget first**

| File | Changes |
|------|---------|
| `tokens.css` | Update spacing, radius, font-size, add glass variables |
| `base.css` | Update base font-size (13px), add `-webkit-backdrop-filter` prefix |
| `layout.css` | Add glass to topbar/nav dropdown, compact padding |
| `components.css` | Refactor .btn/.input/.card/.table — glass surfaces, no borders |

**Step 1.1: Tokens Preview**
- [ ] Update `tokens.css` with compact scale
- [ ] Add glass shadow variables
- [ ] Test dark theme readability

**Step 1.2: Dashboard Widget Preview**
- [ ] Apply glass to `.idx-card`, `.widget-card`, `.card-header`
- [ ] Remove borders from ticker, add surface layering
- [ ] Verify text contrast on glass surfaces
- [ ] Add `-webkit-backdrop-filter` for Firefox/Chrome compatibility

**Step 1.3: Full Core System**
- [ ] Apply glass to all components (.btn, .input, .table, .tabs, .modal)
- [ ] Remove all `border: 1px solid` patterns
- [ ] Add hover effects with shadow elevation
- [ ] Run test suite: `pytest test/ -q`

---

### Phase 2: Layout & Navigation (2 files)
| File | Changes |
|------|---------|
| `layout.css` | Topbar glass, nav hover states, status bar |
| `utilities.css` | Glass utility classes |

- [ ] Add `backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px)` to `.app-topbar`
- [ ] Compact nav padding (12px → 8px vertical)
- [ ] Dropdown menus get glass surface + floating shadow
- [ ] Remove border from `.icon-btn`, use surface elevation instead
- [ ] Run test suite

---

### Phase 3: Feature Pages (13 files)
**Process: Border removal → Glass surface → Hover lift → Test**

| Priority | Files |
|----------|-------|
| **High** | `dashboard.css`, `news.css`, `settings.css` |
| **Medium** | `alerts.css`, `watchlists.css`, `charts.css`, `sentiment.css` |
| **Low** | `fno.css`, `scanners.css`, `market-map.css`, `sector-heatmap.css`, `breadth.css`, `chat.css` |

For each file:
1. Replace `border-bottom` on table rows with `background` hover layering
2. Add `backdrop-filter` to panels/cards
3. Increase padding slightly (compact but not cramped)
4. Run test suite after each file group

---

### Phase 4: Polish & Accessibility
- [ ] Verify both dark/light theme readability
- [ ] Check focus states (outline should remain accessible)
- [ ] Test contrast ratios (WCAG AA minimum)
- [ ] Mobile responsive check
- [ ] Final test suite run

---

## Key Technical Notes

### Browser Compatibility
```css
/* Always use vendor-prefixed version */
backdrop-filter: blur(12px);
-webkit-backdrop-filter: blur(12px); /* Firefox/older Chrome */
```

### Readability Constraint
Glass effects must never reduce text readability:
- Dark mode: `--surface-1` is 4% white → text at 95% luminosity reads clearly
- Light mode: `--surface-1` is 75% white → text at 20% luminosity reads clearly
- Shadow provides depth without obscuring content

### Border Removal Strategy
Instead of borders, use:
- **Surface layering**: Different `--surface-*` levels for separation
- **Padding/gaps**: Space as the separator
- **Shadow on hover**: Elevation change indicates interaction
- **Color shift**: Background opacity change for active/hover states

---

## Estimated Time

| Phase | Files | Est. Time |
|-------|-------|-----------|
| 1. Core system + Dashboard preview | 4 files | 90 min |
| 2. Layout & navigation | 2 files | 30 min |
| 3. Feature pages | 13 files | 90 min |
| 4. Polish & accessibility | All | 30 min |
| **Total** | **25 files** | **~4.5 hours** |

---

## Ready to Start?

Confirm:
1. ✅ Compact spacing scale (reduced 30% from 'spacious')
2. ✅ 13px base font
3. ✅ System fonts only
4. ✅ Glass + -webkit-backdrop-filter
5. ✅ Dashboard widget preview first
6. ✅ Test suite after each phase

Proceed with Phase 1?
