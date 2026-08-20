import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 10, right: 4, bottom: 12, left: 6 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
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
