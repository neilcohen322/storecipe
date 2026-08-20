import { getTheme } from "../tokens";

test("keeps terracotta accent distinct from success and focus rings", () => {
  for (const scheme of ["light", "dark"] as const) {
    const { colors } = getTheme(scheme);
    expect(colors.accent).not.toBe(colors.success);
    expect(colors.focusRing).not.toBe(colors.accent);
    expect(colors.brand).toBe(colors.success);
  }
});

test("documents primary contrast pairs", () => {
  const light = getTheme("light");
  expect(light.colors.canvas).toBe("#f6f1ea");
  expect(light.colors.accent).toBe("#c2410c");
  expect(light.colors.accentContrast).toBe("#ffffff");
  expect(light.colors.focusRing).toBe("#9a3412");

  const dark = getTheme("dark");
  expect(dark.colors.canvas).toBe("#1c1612");
  expect(dark.colors.accent).toBe("#fb923c");
  expect(dark.colors.accentHover).toBe("#ea580c");
  expect(dark.colors.focusRing).toBe("#fdba74");
});

test("tokenizes a minimum overlay scrim for card titles", () => {
  expect(getTheme("light").colors.overlayScrim).toBe("rgba(16, 12, 8, 0.72)");
  expect(getTheme("dark").colors.overlayScrim).toBe("rgba(16, 12, 8, 0.72)");
});
