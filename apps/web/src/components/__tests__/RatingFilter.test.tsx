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

import { RatingFilter } from "../RatingFilter";

test("no minimum is selected when minRating is null", async () => {
  const screen = await render(
    <RatingFilter minRating={null} ratingState="any" onMinRating={jest.fn()} onRatingState={jest.fn()} />,
  );
  expect(screen.getByRole("button", { name: "No minimum" }).props.accessibilityState?.selected).toBe(true);
});

test("min rating chips use human labels", async () => {
  const screen = await render(
    <RatingFilter minRating={null} ratingState="any" onMinRating={jest.fn()} onRatingState={jest.fn()} />,
  );
  for (let rating = 1; rating <= 5; rating += 1) {
    expect(screen.getByRole("button", { name: `${rating} and up` })).toBeTruthy();
  }
  expect(screen.queryByText("minRating")).toBeNull();
});

test("unrated disables min rating chips", async () => {
  const screen = await render(
    <RatingFilter minRating={null} ratingState="unrated" onMinRating={jest.fn()} onRatingState={jest.fn()} />,
  );
  for (let rating = 1; rating <= 5; rating += 1) {
    expect(screen.getByRole("button", { name: `${rating} and up` }).props.accessibilityState?.disabled).toBe(true);
  }
});

test("choosing unrated calls onRatingState", async () => {
  const onRatingState = jest.fn();
  const screen = await render(
    <RatingFilter minRating={null} ratingState="any" onMinRating={jest.fn()} onRatingState={onRatingState} />,
  );
  await fireEvent.press(screen.getByRole("button", { name: "Unrated only" }));
  expect(onRatingState).toHaveBeenCalledWith("unrated");
});
