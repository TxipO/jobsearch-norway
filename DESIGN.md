---
name: Jobsearch Norway
description: A precise, restrained Operate-mode dashboard for one person's Norwegian job search — canon executed at a Todoist/Vercel craft bar.
colors:
  bg: "#F0F4F8"
  elevated: "#FFFFFF"
  raised: "#F7F9FC"
  border: "rgba(15,23,42,0.08)"
  border-mid: "rgba(15,23,42,0.11)"
  border-hi: "rgba(15,23,42,0.16)"
  text: "#0F172A"
  text-sec: "#334155"
  text-muted: "#64748B"
  accent: "#2563EB"
  success: "#16A34A"
  danger: "#DC2626"
  status-yellow: "#A15C00"
  status-orange: "#C2410C"
  status-pink: "#BE185D"
  status-gray: "#6B7280"
typography:
  body:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "0.85rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "0.7rem"
    fontWeight: 600
  meta:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "0.8rem"
    fontWeight: 400
    lineHeight: 1.5
  emphasis:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "0.92rem"
    fontWeight: 600
  headline:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "1.35rem"
    fontSizeNarrow: "1.15rem"
    fontWeight: 700
  mono-numeral:
    fontFamily: "'JetBrains Mono', ui-monospace, 'SFMono-Regular', Consolas, monospace"
    fontFeature: "tabular-nums"
    fontSizeLarge: "1rem"
rounded:
  sm: "6px"
  md: "8px"
  lg: "10px"
spacing:
  xs: "0.25rem"
  sm: "0.5rem"
  md: "0.85rem"
  lg: "1.25rem"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#FFFFFF"
    rounded: "{rounded.md}"
    padding: "0.5rem 1rem"
  status-pill:
    backgroundColor: "transparent"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.sm}"
    padding: "0.3rem 0.55rem"
  score-badge:
    backgroundColor: "transparent"
    textColor: "{colors.text-muted}"
    typography: "{typography.mono-numeral}"
---

# Design System: Jobsearch Norway

## Overview

**Creative North Star: "The Category Standard, Executed Precisely"**

This is not an invented visual world. `PRODUCT.md`'s Brand Commitments record that the user was offered a full visual-world replacement twice (a bokføring-ledger direction, then a postal-sorting-station direction, plus catalog challengers each round) and chose the canon both times. The standing craft bar is **Todoist** (compact, clear list ergonomics) and **Vercel Dashboard** (precise, restrained, tabular-numeral data density). The system's identity is that it refuses a metaphor: it is a personal Operate dashboard for triaging real job vacancies, built to be scanned fast and trusted completely, not admired.

The redesign covers every surface: topbar, sync bar, filters, vacancy list, kanban board, vacancy detail, and the resume-prompt page all run on the same root tokens and the same component-level rules — there is no legacy tier left pending reconciliation. The one item that remains genuinely open is `.filters-panel`'s drop shadow (see Elevation & Depth) — not unfinished work, but a deliberate one-off that the system documents explicitly rather than silently allowing to drift.

A subsequent `/impeccable audit` pass (2026-08-04) closed a round of accessibility gaps: a failing-contrast text token was retired outright, two heading-less pages gained real `<h1>` elements, notes got a screen-reader-only text companion to their icon+tooltip, mobile touch targets were floored at 44px, the topnav grew a real active state, and both bare-icon form fields (`textarea`, `#prompt-box`) got `aria-label`. None of this is decoration — it's the same "trusted completely" bar applied to assistive tech and touch input, not just sighted mouse users.

A further `/impeccable animate` pass (2026-08-04) gave the app its first real motion system: six previously-static hover states now animate, the htmx-swap crossfade on the three most-repeated interactions (status/flag/notes updates) got its one authored focal moment, and the sync button's disabled state pulses instead of sitting inert during a multi-minute sync. See Elevation & Depth's Motion subsection — motion here is exclusively feedback for state and continuity, never page-load choreography or decorative reveal, matching the Operate-mode thesis.

**Key Characteristics:**
- Restrained neutral-plus-one-accent palette, light/dark via `prefers-color-scheme`, no manual toggle.
- Every number is set in tabular-mono digits; every status has its own hue.
- Small color-tier dots instead of tinted badge pills for score, sharp small radii, real focus rings.
- Every interactive surface (button, chip, form field, heading) is verified accessible by measurement, not by eye — contrast math, real headings, real labels, a real touch-target floor.
- Motion is CSS-only, restrained to feedback (hover, swap, in-progress state) — no page-load or decorative animation.

## Colors

A cool, near-white neutral scale in light mode (near-black in dark mode) with a single blue accent and a hand-picked hue per pipeline status.

### Primary
- **Accent Blue** (`#2563EB`, dark: `#5B9BFF`): primary actions (sync button, submit/filter buttons, active filter state, topnav active-page indicator), links, the default color for the `new`/`archived` statuses.

### Neutral
- **Page** (`#F0F4F8`, dark `#0A0A0F`): page background (`--c-bg`).
- **Elevated** (`#FFFFFF`, dark `#12121A`): topbar, list container, panels, vacancy-detail, kanban columns (`--c-elevated`).
- **Raised** (`#F7F9FC`, dark `#17171F`): input fields, chips, secondary surfaces one step above the page — score-breakdown, vacancy-facts, borrowed-notice, notes textarea, kanban cards, `#prompt-box` (`--c-raised`).
- **Border / Border-mid / Border-hi** (`rgba(15,23,42, 0.08 / 0.11 / 0.16)`, inverted alpha-on-white in dark mode): a three-step hairline border scale — `border` for dividers between rows and panel outlines, `border-mid` for chip/input/card outlines, `border-hi` for stronger input/interactive outlines.
- **Text / Text-sec / Text-muted** (`#0F172A` / `#334155` / `#64748B`): a three-step text scale from primary reading text down to the most muted label or secondary mark (separators, sub-labels, `reach-none`, `kanban-empty`, `kanban-count`, `notes-saved-tag`, `notes-indicator`, `.vacancy-meta .sep`/`.lang-tag`/`.source-tag`). A fourth, lighter step (`--c-text-sub`, `#94A3B8`) existed through the first two redesign passes but was removed in the 2026-08-04 accessibility audit: hand-measured WCAG relative-luminance contrast put it at 2.56:1 in light mode and 3.92:1 in dark mode against elevated surfaces — both fail the 4.5:1 text minimum, and light mode even failed the 3:1 non-text minimum. There is no reservation case for a fourth, lighter step; every former `--c-text-sub` usage now runs on `--c-text-muted` (re-verified at 4.76:1 light / 7.27:1 dark, both pass). Don't reintroduce a text color lighter than `--c-text-muted` — the neutral scale bottoms out there by design, not by omission.

### Named Rules
**The Status-Hue Rule.** Every pipeline status (`interesting`, `applied`, `interview`, `offer`, `rejected`, `ignored`) gets its own accent hue (`--c-yellow`, `--c-orange`, `--c-pink`, `--c-success`, `--c-danger`, `--c-gray`) at 15% tinted background + full-strength text, applied only to the active `.status-btn`. `new` and `archived` share the default accent blue. This holds identically on the kanban board (`.kanban-card .status-control`) as on the list row — the pipeline reads at a glance without reading the label anywhere in the app; don't collapse statuses back onto one color to "simplify."

## Typography

**Body Font:** Inter (with -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif fallback)
**Label/Mono Font:** JetBrains Mono (with ui-monospace, "SFMono-Regular", Consolas, monospace fallback)

**Character:** A workhorse UI sans (Inter) for everything read as prose or label, paired with a monospace numeral face reserved strictly for measured/compared values. Inter is used deliberately here despite being flagged "overused" by the project's own design-hook detector — this is a disclosed choice for the canon/Operate path (it is what Todoist and Vercel Dashboard both use), not an oversight, and should not be swapped out by a future pass without a reason.

### Hierarchy
- **Headline** (700, 1.35rem desktop / 1.15rem at ≤640px, `.detail-header h1` only): the vacancy-detail page's visible heading. Two other pages (`index.html`, `kanban.html`) now carry a real `<h1 class="sr-only">` ("Список вакансій" / "Kanban") for heading-based assistive-tech navigation, visually hidden because the topbar brand already carries the visible page identity — this is a structural/accessibility addition, not a second visible headline style; any future heading-less page should follow the same `sr-only` pattern rather than either adding a redundant visible heading or leaving the page headingless.
- **Emphasis** (600, 0.92rem): the one line per component that must out-scan its neighbors without becoming a heading — vacancy-row title, score-badge numeral, the brand wordmark (0.95rem, a deliberate single-context bump for the topbar only). Not a scale step to reuse freely; it's reserved for "the thing you read first in this row."
- **Body** (400, 0.85rem, line-height 1.5): default reading text, descriptions, score-breakdown/vacancy-facts body copy.
- **Meta** (400, 0.78–0.88rem): secondary/contextual info that sits below or beside the primary line — vacancy meta line, sync-status, filter-row labels, kanban-card-meta, kanban-empty state, prompt-hint. Documented as one role with component jitter, not a growing list of separate sizes; new meta text should target 0.8rem exactly rather than adding another nearby value.
- **Label** (600, 0.7–0.72rem): field labels, filter labels, status/flag button text, kanban-count — always with muted text color at rest.
- **Numeral** (JetBrains Mono, tabular-nums, weight varies 500–700, size varies 0.7–1rem by context): score %, salary, extent %, dates, sync-delta counts, pagination counts, score-breakdown point deltas, vacancy-facts figures, kanban-count. The score badge has a documented large variant at `1rem` on the vacancy-detail page (`.vacancy-detail .score-badge`) versus `0.9rem` on the list row — same role, a deliberate size bump because the detail page has room to let the primary metric read bigger.

### Named Rules
**The Tabular-Numeral Rule.** Any value that is measured, ranked, or compared — score, salary, extent %, dates, sync deltas, pagination, score-breakdown point deltas (`+5`/`-3`), vacancy-facts figures (deadline, extent %, salary, transit duration), kanban card counts — is set in `.num` (JetBrains Mono, `font-variant-numeric: tabular-nums`), never in the body sans. This is a genuinely app-wide rule, not scoped to the list page: it holds on the list, the kanban board, and the vacancy-detail page identically. Digits align in a column like a real metric readout, they don't just "look monospaced." Prose is never set in mono. `#prompt-box` (the generated søknad-prompt textarea) also runs on the shared `--font-mono` token rather than its own separate stack, for the same "one mono identity" reason even though its content isn't tabular numerals.

## Layout

Single-column content capped at `max-width: 1100px`, centered, with `1.5rem` page padding (`0.9rem` under 640px) — except the kanban page, which drops the cap (`main:has(.kanban-board) { max-width: none; }`, user-requested 2026-08-02) and runs the full viewport, since a multi-column board is exactly the case a fixed reading-width column fights rather than serves. The vacancy list is a bordered container of grid rows (`grid-template-columns: 56px 1fr`, `46px 1fr` under 640px — score column, then main content), not loose flex, with one hairline divider (`--c-border`) between rows and none after the last. The kanban board is `grid-auto-flow: column` (never a hardcoded column count, so adding a status can't silently overflow the row) collapsing to a single-column stack under 640px. It shows 5 of the 8 statuses as columns (`interesting` through `rejected`) — `new` was always excluded (that's the main list's job), and `ignored`/`archived` were dropped 2026-08-02 as terminal dead-end states that don't need board space; both stay reachable as status-pill actions on every card and via the main list's status filter, they just aren't columns. Interactive control clusters (filters, status pills, sync stats) use small flex gaps (`0.25–0.75rem`) rather than a single large gap scale — the rhythm is tight and information-dense, matching the Todoist/Vercel density target, and holds on the detail page's `.actions`/`.detail-header` clusters too.

### Named Rules
**The 44px Touch-Target Floor.** Inside the `@media (max-width: 640px)` block only, `.status-btn`, `.flag-btn`, and `.notes-save-btn` get `min-height: 44px` plus `display: inline-flex; align-items: center; justify-content: center` (WCAG 2.5.5). Desktop density is deliberately unchanged — the compact pill sizing that is the point of the Todoist/Vercel bar stays put above 640px; the floor is scoped narrowly to touch-primary viewports rather than resizing every pill everywhere. Any new small tap target added to a mobile layout should follow the same scoped pattern rather than growing the desktop component.

## Elevation & Depth

Flat by default: surfaces are told apart by a background step (`bg` → `raised` → `elevated`) and a 1px border, not shadows — this holds for every card-like block in the app, including score-breakdown, vacancy-facts, borrowed-notice, and kanban cards. The one exception, app-wide, is the floating `.filters-panel` (a `position: absolute` dropdown), which gets a real drop shadow (`0 8px 20px rgba(0,0,0,0.2)`, sidecar token `panel-float`) because it visually detaches from the page flow and needs to read as floating above the list, not as another flat card. The shadow's color is intentionally a neutral black at low opacity rather than a palette color — it is not a surface, text, or accent token, it's a one-off elevation cue used exactly once, and is recorded here (and in the sidecar's `extensions.shadows`) rather than forced into the named color palette.

### Named Rules
**The Flat-Surface Rule.** Depth is conveyed by background-tone stepping and 1px borders, not shadow. Shadow is reserved for elements that genuinely float above the page (popovers, dropdown panels) — never applied to cards or rows at rest, on any page.

### Motion

Added whole in a `/impeccable animate` pass (2026-08-04) — before this the app had zero authored transitions. Motion is CSS-only (transitions plus one `@keyframes` loop), adding no dependency, and is scoped strictly to feedback for state and continuity — there is no page-load choreography and no decorative reveal anywhere in the app, matching the Operate-mode thesis that this is a tool to trust, not a surface to admire.

- **Hover feedback (130ms ease-out).** Six previously-transitionless hover states — `.topnav a` (color, border-color), `.filters-reset` (background-color, color), `.vacancy-row` (background-color), `.notes-save-btn` (opacity), `.back-link` (color), `.kanban-card-title` (color) — now animate on a single shared fast duration, named per-property rather than `transition: all` (matching the existing per-property discipline on `.status-btn`/`.flag-btn`/`.lang-toggle a`, see Components → Buttons).
- **The swap crossfade (180ms, `cubic-bezier(0.16, 1, 0.3, 1)`).** The app's one authored focal moment, applied to the single most-repeated interaction in the product: status-pipeline clicks, flag toggles, and notes saves. `_status_control.html`, `_flag_control.html` (its button is now wrapped in a real `.flag-wrap` span instead of an unclassed one), and `_notes_control.html` all swap with `hx-swap="outerHTML settle:180ms"`; `.status-control`, `.flag-wrap`, `.notes-control` transition `opacity`, and htmx's own `.htmx-settling` class (held for 180ms on the freshly-swapped node) starts it at `opacity: 0` so the new state materializes rather than popping in. Deliberately no fade-out delay on the outgoing node — only the incoming node fades in — because this fires on every single click and latency was judged worse than a slightly less complete crossfade.
- **Sync-in-progress pulse.** `.sync-btn:disabled` runs `animation: sync-pulse 1.6s ease-in-out infinite` (opacity `0.55` ↔ `0.85`, no scale or position change) instead of sitting static at `0.55` opacity, because the sync itself can run for minutes and a static dimmed button gave zero sense that work was happening. Has a `@media (prefers-reduced-motion: reduce)` fallback that removes the animation entirely.

### Named Rules
**The Feedback-Only Motion Rule.** Every authored transition or animation in the app exists to confirm a state change (hover, swap, in-progress) or preserve continuity across an htmx swap — never to choreograph page load or decorate an otherwise-static element. Hover transitions hold to a single shared `130ms ease-out`, named per affected property; the one deliberately slower, deliberately eased moment (`180ms`, `cubic-bezier(0.16, 1, 0.3, 1)`) is reserved for the htmx-swap crossfade because it is the app's single most-repeated interaction and earns the extra polish. Any looping animation (the sync pulse is the only one that exists) must ship a `prefers-reduced-motion: reduce` fallback. Don't add a page-load reveal, a staggered list-in animation, or decorative motion on a static element — none of that fits an Operate-mode tool built to be scanned and trusted, not watched.

## Shapes

A sharp small-radius scale, deliberately capped: `--radius-sm` (6px, status pills, tags, focus-ring corners, lang-toggle), `--radius-md` (8px, buttons, inputs, filter chips, notes-save-btn), `--radius-lg` (10px, panels, the vacancy-list container, vacancy-detail, score-breakdown, vacancy-facts, borrowed-notice, notes-textarea, kanban-column, kanban-card, `#prompt-box`). There are zero radius values above 10px anywhere in the app — the detail/kanban components that previously carried ad hoc larger radii (12–16px) were moved onto this scale in an earlier pass, and a `--radius-xl` token was removed after the finish review flagged it as an unused ceiling-breach. Nothing should reintroduce a radius above 10px on any surface.

## Components

### Buttons
- **Shape:** 8px radius (`--radius-md`).
- **Primary:** solid accent-blue background, white text, `0.5rem 1rem` padding (`sync-btn`, `filters-submit`, `apply-btn`, resume-prompt's copy button); `opacity: 0.88` on hover, `0.55` + `not-allowed` cursor when disabled. The disabled sync button additionally pulses opacity — see Elevation & Depth → Motion.
- **Ghost/Reset:** transparent background, `border-hi` outline, muted text; hovers to `--c-hover` background + `text-sec` (`filters-reset`).
- **Focus:** every button gets the shared focus-visible ring (see Do's and Don'ts) — this project had no visible focus treatment before the redesign.
- **Transitions:** hover/active state changes are named per-property (`background-color`, `color`, `border-color` as applicable to the selector), not a blanket `transition: all` — a performance-hygiene cleanup from the 2026-08-04 audit on `.status-btn`, `.flag-btn`, `.lang-toggle a`, and the same discipline extended to every hover transition added in the subsequent motion pass. No visual change; new interactive elements should name their transitioned properties rather than reach for `all`.

### Status & Filter Pills (signature component)
- **Style:** compact ghost pills — transparent background, 1px `border-mid` outline, `--radius-sm` (6px), `0.72–0.85rem` label text.
- **State:** active state swaps to a 15% tint of the relevant hue as background plus full-strength text color and a transparent border (`color-mix(in srgb, var(--c-hue) 15%, transparent)`). This same tinted-pill pattern is reused for `.status-btn` (list row and kanban card alike), `.lang-toggle a`, and the sync-stat chips — one consistent "selected chip" language across the app.

### Score Badge (signature component)
- **Style:** right-aligned tabular-mono numeral plus a small 6px color-tier dot (`::before`) — not a tinted background pill. The number carries the primary read; the dot backs it up for color-blind scanning without repeating color as a background fill. Three tiers: `score-high` (≥55, success green), `score-mid` (25–54, accent blue), `score-low` (<25, `--c-text-muted`). The vacancy-detail page runs the same component at a documented larger size (`1rem` vs the list row's `0.9rem`) — see Typography's Numeral hierarchy.

### Cards / Containers
- **Corner Style:** 10px (`--radius-lg`) for the list container, floating panels, vacancy-detail, score-breakdown, vacancy-facts, borrowed-notice, kanban-column, and kanban-card.
- **Background:** `--c-elevated` for primary containers (vacancy-list, vacancy-detail, kanban-column), `--c-raised` for nested/secondary blocks (sync-stat chips, notes textarea, score-breakdown, vacancy-facts, borrowed-notice, kanban-card, `#prompt-box`).
- **Shadow Strategy:** none at rest; see Elevation & Depth.
- **Border:** 1px `--c-border` (containers) or `--c-border-mid`/`--c-border-hi` (interactive elements needing a stronger outline).

### Inputs / Fields
- **Style:** 1px `--c-border-hi` border, `--radius-md` (8px), `--c-raised` background, `0.5rem 0.75rem` padding. Numeric inputs (`type="number"`) are set in `--font-mono`. The notes textarea is the one exception at `--radius-lg` (10px), matching the card-scale surfaces it sits inside rather than the input scale.
- **Focus:** border shifts to `--c-accent`, plus the global focus-visible ring on keyboard focus.
- **Labeling.** Every textarea and input gets a real accessible label. Where a visible `<label>` would disrupt the compact layout, it's a real `aria-label` instead of relying on a `placeholder` alone — `.notes-textarea` ("Нотатки по цій вакансії") and `#prompt-box` ("Текст промпту для копіювання") both do this as of the 2026-08-04 audit. This is a standing commitment for the app, not a one-off fix: any future textarea or input needs a visible label or a real `aria-label`, never a placeholder-only field.

### Navigation
- **Topbar:** `--c-elevated` background, bottom `--c-border` hairline, brand wordmark (700 weight, -0.01em tracking) left, nav links right. Nav links are muted-text, 2px transparent bottom border at rest, darken to `--c-text` on hover (130ms, see Elevation & Depth → Motion).
- **Active state:** the current page's nav link gets both `aria-current="page"` (assistive-tech semantics, driven server-side off `request.url.path` in `base.html`) and `class="active"` (`color: var(--c-text)`, bottom border switches to `--c-accent`). Added in the 2026-08-04 audit — before this the topnav had no way, visual or semantic, to tell you which page you were on.
- **Back link:** muted text, no underline at rest, darkens to `--c-text-sec` on hover (`.back-link`, used on both detail and resume-prompt pages).

### Icons
- **Style:** authored inline SVG, stroke-based (1.5px stroke-width, ~13-16px box, round line-cap/join), `currentColor` stroke, no fill. Used for the flag, notes, document, and backup-warning indicators. Emoji were deliberately replaced by this icon system across every template in the app — see Do's and Don'ts. There are zero emoji characters anywhere in `web/templates/*.html`; this is a completed guarantee, not a per-instance fix, and any new indicator must be authored in this same SVG language rather than reaching for an emoji glyph.
- **Accessibility companion.** Icon-only indicators that convey information via `title` (a hover tooltip) also carry the same text as a `<span class="sr-only">` sibling where the information is dynamic and specific enough to matter — the notes indicator on the list and kanban card now reads `Нотатка: {{ v.notes }}` to screen readers, not just to mouse hover. `title` alone is not sufficient for content a screen-reader user needs; icon-only affordances that carry real information should pair `title` with an `sr-only` text echo, following this precedent.

## Do's and Don'ts

### Do:
- **Do** set every measured/compared value (score, salary, %, dates, counts, score-breakdown deltas, vacancy-facts figures) in `.num` — JetBrains Mono, tabular-nums. This is the system's signature discipline, and it holds on every page.
- **Do** give every status its own hue at 15% tint background + full-strength text; keep `new`/`archived` on the default accent, on both the list row and the kanban card.
- **Do** give every interactive element (`a`, `button`, `input`, `select`, `summary`, `textarea`) a real `:focus-visible` ring (`box-shadow: 0 0 0 2px var(--c-bg), 0 0 0 4px var(--c-accent)`).
- **Do** keep new radii within the 6/8/10px scale (`--radius-sm/md/lg`); there is no larger token, and none of the shipped surfaces exceed it.
- **Do** author new icons as inline stroke-based SVG (1.5px stroke, ~13-16px), matching the flag/notes/doc/backup-warning icon language.
- **Do** verify text-color-on-background contrast against real WCAG relative-luminance math for any new muted/low-emphasis text pairing, not by eye — this discipline caught a genuine 4.5:1/3.92:1 double failure (`--c-text-sub`, removed entirely) this way.
- **Do** give every page a real `<h1>` (visible, or `class="sr-only"` where the topbar already carries the visible identity) and a real `aria-label`/`<label>` on every textarea/input.
- **Do** keep interactive controls at a 44px touch-target floor on mobile viewports, without inflating the desktop density that's the point of the redesign.
- **Do** name transitioned properties explicitly (`background-color`, `color`, `border-color`, `opacity`) rather than `transition: all`, and hold new hover feedback to the shared `130ms ease-out` unless it's the htmx-swap crossfade.
- **Do** give any looping/decorative-adjacent animation (like the sync pulse) a `prefers-reduced-motion: reduce` fallback.

### Don't:
- **Don't** introduce a text color lighter/lower-contrast than `--c-text-muted`; the neutral text scale bottoms out there deliberately after `--c-text-sub` failed WCAG AA and was removed — there is no reservation case that brings a fourth, lighter step back.
- **Don't** use a tinted background pill for the score badge — the dot-plus-mono-numeral pattern is deliberate, avoiding "badge soup."
- **Don't** use emoji as UI iconography anywhere in the app; the last three (backup-warning, doc-btn, kanban notes-indicator) were replaced with authored SVG in an earlier pass.
- **Don't** add a shadow to a card or row at rest; shadow is reserved for the one genuinely floating element (the filters panel), and its neutral-black low-opacity color is a documented one-off, not a pattern to repeat with other colors.
- **Don't** introduce a radius above 10px on any surface; a `--radius-xl` token was removed for exactly this reason.
- **Don't** invent a new visual world for future surfaces without the user re-opening that choice — the standing decision is canon at the Todoist/Vercel bar (see Overview).
- **Don't** ship a textarea or input with only a `placeholder` and no real label — use `aria-label` when a visible `<label>` doesn't fit the layout.
- **Don't** add page-load choreography, staggered reveals, or decorative motion on a static element; motion here is feedback-only (hover, swap, in-progress state), matching the Operate-mode thesis.
