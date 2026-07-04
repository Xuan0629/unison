---
name: Heritage
colors:
  primary: "hsl(0, 0%, 88%)"
  secondary: "hsl(0, 0%, 47%)"
  tertiary: "hsl(38, 70%, 60%)"
  neutral: "hsl(0, 0%, 4%)"
  surface:
    bg: "hsl(0, 0%, 4%)"
    card: "hsl(0, 0%, 8%)"
    sidebar: "hsl(0, 0%, 5%)"
    raised: "hsl(0, 0%, 11%)"
  semantic:
    red: "hsl(0, 65%, 55%)"
    orange: "hsl(30, 80%, 52%)"
    blue: "hsl(210, 60%, 55%)"
    purple: "hsl(265, 55%, 58%)"
    green: "hsl(120, 40%, 50%)"
typography:
  body: { fontFamily: "system-ui, sans-serif", fontSize: "14px" }
  heading: { fontFamily: "system-ui, sans-serif", fontSize: "20px", weight: "600" }
  mono: { fontFamily: "ui-monospace, monospace", fontSize: "13px" }
rounded:
  sm: "4px"
  md: "8px"
  lg: "12px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  xxl: "32px"
  xxxl: "48px"
border:
  default: "hsl(0, 0%, 16%)"
  focus: "hsl(38, 70%, 55%)"
---

## Overview

Dark-first dashboard design system. High-contrast amber accent on deep surfaces.
Agent-native: compact info density, breathing glow for active states, zero-flicker DOM patching.

## Colors

- **Primary (#E0E0E0):** Main text — high contrast on dark surfaces
- **Secondary (#787878):** Dimmed labels, captions, metadata
- **Tertiary (Amber):** Accent — buttons, active states, highlights
- **Neutral (#0A0A0A):** Deep background, avoids pure black eye strain

## Typography

One family across all surfaces. Mono for code/data. System font stack — no webfont requests.

## Spacing

4px scale grid. Consistent padding/margin across components. Sidebar: 280px fixed.

## Components

- Status cards: phase/iteration/verdict — key metrics first
- Token gauges: SVG ring per agent, gold (dark) / blue (light)
- Timeline: horizontal phase-transition dots
- Control panel: pause/skip/report buttons with accent hover
- Active panel: breathing glow pulse while working
- Error panel: halt signal + commit hash copy

## Design Rules for Agents

1. Never inline styles — use CSS custom properties
2. Match existing BEM modifiers, don't create new variants
3. 4.5:1 contrast minimum for all text
4. Responsive: sidebar → horizontal strip below 768px
5. JS: diff-based DOM patching, zero flicker
