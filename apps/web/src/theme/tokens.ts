import type { ResolvedScheme, Theme, ThemeColors } from "./types";

export const HEADING_FONT_FAMILY = "Fraunces_700Bold";

const shared = {
  spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, "2xl": 48 },
  sizing: { control: 44, icon: 24, touchTarget: 48 },
  radii: { sm: 8, md: 12, lg: 16, pill: 999 },
  type: {
    caption: 12,
    body: 15,
    subtitle: 18,
    heading: 28,
    display: 54,
    fontFamily: { heading: HEADING_FONT_FAMILY },
  },
  breakpoints: { compact: 0, medium: 768, expanded: 1200 },
  motion: { fast: 150, normal: 250, slow: 400 },
  shadows: {
    none: { elevation: 0, shadowColor: "transparent", shadowOpacity: 0, shadowRadius: 0, shadowOffset: { width: 0, height: 0 } },
    raised: { elevation: 3, shadowColor: "#000000", shadowOpacity: 0.16, shadowRadius: 8, shadowOffset: { width: 0, height: 3 } },
  },
} as const;

const palettes: Record<ResolvedScheme, ThemeColors> = {
  light: {
    canvas: "#f6f1ea",
    surface: "#ffffff",
    elevatedSurface: "#f3ece3",
    text: "#1c1410",
    mutedText: "#6b5748",
    border: "#e4d8cc",
    accent: "#c2410c",
    accentHover: "#9a3412",
    accentContrast: "#ffffff",
    brand: "#2d6a4f",
    success: "#2d6a4f",
    warning: "#b7791f",
    danger: "#b42318",
    focusRing: "#9a3412",
    scrim: "rgba(28, 20, 16, 0.48)",
    overlayScrim: "rgba(16, 12, 8, 0.72)",
  },
  dark: {
    canvas: "#1c1612",
    surface: "#2a221c",
    elevatedSurface: "#362c24",
    text: "#f6f1ea",
    mutedText: "#c4b5a5",
    border: "#4a3c32",
    accent: "#fb923c",
    accentHover: "#ea580c",
    accentContrast: "#1c1612",
    brand: "#74c69d",
    success: "#74c69d",
    warning: "#f6bd60",
    danger: "#f4a3a3",
    focusRing: "#fdba74",
    scrim: "rgba(0, 0, 0, 0.56)",
    overlayScrim: "rgba(16, 12, 8, 0.72)",
  },
};

export function getTheme(scheme: ResolvedScheme): Theme {
  return { ...shared, colors: palettes[scheme] };
}
