import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 10, right: 4, bottom: 12, left: 6 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
  useTheme: () => ({
    theme: {
      colors: {
        canvas: "#f7fff9",
        surface: "#fff",
        elevatedSurface: "#fff",
        text: "#10231c",
        mutedText: "#527060",
        border: "#d0e5d6",
        accent: "#2d6a4f",
        accentHover: "#1b4332",
        accentContrast: "#fff",
        success: "#2d6a4f",
        warning: "#b7791f",
        danger: "#b42318",
        focusRing: "#40916c",
        scrim: "rgba(0,0,0,.4)",
      },
      spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, "2xl": 48 },
      sizing: { control: 44, icon: 24, touchTarget: 48 },
      radii: { sm: 8, md: 12, lg: 16, pill: 999 },
      type: { caption: 12, body: 15, subtitle: 18, heading: 28, display: 54 },
    },
  }),
}));

import { DurationFilter } from "../DurationFilter";

test("any duration is default and max can be selected", async () => {
  const onChange = jest.fn();
  const screen = await render(
    <DurationFilter observed={{ min: 15, max: 90 }} value={null} onChange={onChange} />,
  );
  expect(screen.getByRole("button", { name: "Any duration" }).props.accessibilityState?.selected).toBe(true);
  expect(onChange).not.toHaveBeenCalled();
  await fireEvent.press(screen.getByRole("button", { name: "90 minutes" }));
  expect(onChange).toHaveBeenCalledWith(90);
  onChange.mockClear();
  await fireEvent.press(screen.getByRole("button", { name: "Any duration" }));
  expect(onChange).toHaveBeenCalledWith(null);
});

test("unavailable time when observed is null", async () => {
  const onChange = jest.fn();
  const screen = await render(
    <DurationFilter observed={null} value={45} onChange={onChange} />,
  );
  expect(screen.getByText("unavailable")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Decrease duration" })).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Clear 45 minutes" }));
  expect(onChange).toHaveBeenCalledWith(null);
});

test("out of range bookmark is not clamped until edited", async () => {
  const onChange = jest.fn();
  const screen = await render(
    <DurationFilter observed={{ min: 15, max: 90 }} value={999} onChange={onChange} />,
  );
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Any duration" })).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Decrease duration" }));
  expect(onChange).toHaveBeenCalledTimes(1);
  const next = onChange.mock.calls[0][0] as number;
  expect(next).toBeGreaterThanOrEqual(0);
  expect(next).toBeLessThanOrEqual(90);
});

test("zero max range after leaving any duration", async () => {
  const onChange = jest.fn();
  const screen = await render(
    <DurationFilter observed={{ min: 0, max: 0 }} value={null} onChange={onChange} />,
  );
  await fireEvent.press(screen.getByRole("button", { name: "0 minutes" }));
  expect(onChange).toHaveBeenCalledWith(0);
});
