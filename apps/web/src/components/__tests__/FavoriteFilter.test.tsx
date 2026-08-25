import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 10, right: 4, bottom: 12, left: 6 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

import { FavoriteFilter } from "../FavoriteFilter";

test("all recipes is selected when favorite is unset", async () => {
  const screen = await render(<FavoriteFilter value={undefined} onChange={jest.fn()} />);
  expect(screen.getByRole("button", { name: "All recipes" }).props.accessibilityState?.selected).toBe(true);
  expect(screen.getByRole("button", { name: "Favorites only" }).props.accessibilityState?.selected).toBe(false);
});

test("favorites only is selected when the filter is active", async () => {
  const onChange = jest.fn();
  const screen = await render(<FavoriteFilter value={true} onChange={onChange} />);
  expect(screen.getByRole("button", { name: "Favorites only" }).props.accessibilityState?.selected).toBe(true);
  await fireEvent.press(screen.getByRole("button", { name: "All recipes" }));
  expect(onChange).toHaveBeenCalledWith(undefined);
});

test("choosing favorites only calls onChange with true", async () => {
  const onChange = jest.fn();
  const screen = await render(<FavoriteFilter value={undefined} onChange={onChange} />);
  await fireEvent.press(screen.getByRole("button", { name: "Favorites only" }));
  expect(onChange).toHaveBeenCalledWith(true);
});
