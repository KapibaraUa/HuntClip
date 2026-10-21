# Design System — HuntClip (powered by Hallmark)

A locked, anti-AI-slop design system for HuntClip. Every UI view and component reads this system.
Adheres strictly to the Hallmark standard: zero generic templates, honest copy, locked tokens, 8-state interactive discipline, and precision workbench ergonomics.

## Genre
**Modern-Minimal Studio Workbench** (Linear / CapCut Desktop / Vercel Pro aesthetic).
An instrument panel for autonomous video extraction, not a generic landing page template.

## Theme Tokens
- `--color-paper`: `oklch(12% 0.012 260)` (#0B0E14) — deep obsidian ground
- `--color-paper-2`: `oklch(16% 0.015 260)` (#131924) — elevated workbench surface
- `--color-paper-3`: `oklch(20% 0.018 260)` (#182232) — nested tiles and inputs
- `--color-ink`: `oklch(98% 0.002 260)` (#F8FAFC) — high-contrast text
- `--color-ink-2`: `oklch(72% 0.02 260)` (#94A3B8) — secondary labels and hints
- `--color-rule`: `rgba(255, 255, 255, 0.08)` — hairline grid rules and splitters
- `--color-rule-focus`: `oklch(62% 0.22 264)` (#6366F1) — indigo focus indicator
- `--color-accent`: `oklch(62% 0.22 264)` (#6366F1) — electric signal accent (< 5% viewport area)
- `--color-success`: `oklch(70% 0.17 155)` (#10B981) — emerald completed/accepted mark

## Typography
- **Display & Headings**: Inter, weights 700 / 800, roman only (`font-style: normal`), `letter-spacing: -0.03em`. No italic display text.
- **Body & Controls**: Inter, weights 400 / 500 / 600, 14–15px base, `line-height: 1.5`.
- **Machine & Label Voice**: JetBrains Mono, weights 500 / 600, `0.06em` tracking, uppercase for status tags, timestamps, token counts, and keybindings.

## Spacing & Geometry
- Strict 8-point geometric scale: `--space-2xs: 2px`, `--space-xs: 4px`, `--space-sm: 8px`, `--space-md: 16px`, `--space-lg: 24px`, `--space-xl: 32px`.
- Hairline-driven: 1px rules define layout compartments; zero heavy blurred drop-shadow blobs.
- Corner radii: `--radius-xs: 4px`, `--radius-sm: 6px`, `--radius-md: 10px`, `--radius-lg: 14px`.

## Interactive Hallmark Disciplines
1. **Working Command Palette (Ctrl+K / ⌘K)**: A real command palette overlay with keyboard navigation, fuzzy filtering, and instant shortcuts (switch views, toggle CapCut, open clips folder, jump to settings).
2. **8-State Interactive Completeness**: Every button, chip, and input provides explicit visual styling for:
   `default`, `hover`, `:focus-visible` (2px focus ring with 2px offset), `active`, `disabled`, `loading`, `error`, `success`.
3. **Honest Copy**: Real stream timecodes, genuine token calculations, and factual labels. Zero fabricated marketing metrics.
4. **Mobile & Viewport Hard Floor**: Flawless responsive collapse down to 320px with zero horizontal scroll (`overflow-x: clip`).
