import type { ResolvedScheme, Theme, ThemeColors } from "./types";

const shared = {
  spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, "2xl": 48 },
  sizing: { control: 44, icon: 24, touchTarget: 48 },
  radii: { sm: 8, md: 12, lg: 16, pill: 999 },
  type: { caption: 12, body: 15, subtitle: 18, heading: 28, display: 54 },
  breakpoints: { compact: 0, medium: 768, expanded: 1024 },
  motion: { fast: 150, normal: 250, slow: 400 },
  shadows: {
    none: { elevation: 0, shadowColor: "transparent", shadowOpacity: 0, shadowRadius: 0, shadowOffset: { width: 0, height: 0 } },
    raised: { elevation: 3, shadowColor: "#000000", shadowOpacity: 0.16, shadowRadius: 8, shadowOffset: { width: 0, height: 3 } },
  },
} as const;

const palettes: Record<ResolvedScheme, ThemeColors> = {
  light: {
    canvas: "#f7fff9", surface: "#ffffff", elevatedSurface: "#ffffff", text: "#10231c", mutedText: "#527060", border: "#d0e5d6",
    accent: "#2d6a4f", accentHover: "#1b4332", accentContrast: "#ffffff", success: "#2d6a4f", warning: "#b7791f", danger: "#b42318", focusRing: "#40916c", scrim: "rgba(16, 35, 28, 0.48)",
  },
  dark: {
    canvas: "#10231c", surface: "#1a3329", elevatedSurface: "#244537", text: "#f7fff9", mutedText: "#b7d8c7", border: "#2d6a4f",
    accent: "#52b788", accentHover: "#74c69d", accentContrast: "#10231c", success: "#74c69d", warning: "#f6bd60", danger: "#f4a3a3", focusRing: "#74c69d", scrim: "rgba(0, 0, 0, 0.56)",
  },
};

export function getTheme(scheme: ResolvedScheme): Theme {
  return { ...shared, colors: palettes[scheme] };
}
