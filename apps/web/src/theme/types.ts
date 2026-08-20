export type ThemePreference = "light" | "dark" | "system";
export type ResolvedScheme = "light" | "dark";

export type ThemeColors = {
  canvas: string;
  surface: string;
  elevatedSurface: string;
  text: string;
  mutedText: string;
  border: string;
  accent: string;
  accentHover: string;
  accentContrast: string;
  brand: string;
  success: string;
  warning: string;
  danger: string;
  focusRing: string;
  scrim: string;
  overlayScrim: string;
};

export type ThemeType = {
  caption: number;
  body: number;
  subtitle: number;
  heading: number;
  display: number;
  fontFamily: { heading: string };
};

export type Theme = {
  colors: ThemeColors;
  spacing: Record<"xs" | "sm" | "md" | "lg" | "xl" | "2xl", number>;
  sizing: Record<"control" | "icon" | "touchTarget", number>;
  radii: Record<"sm" | "md" | "lg" | "pill", number>;
  type: ThemeType;
  breakpoints: Record<"compact" | "medium" | "expanded", number>;
  motion: Record<"fast" | "normal" | "slow", number>;
  shadows: Record<"none" | "raised", { elevation: number; shadowColor: string; shadowOpacity: number; shadowRadius: number; shadowOffset: { width: number; height: number } }>;
};
