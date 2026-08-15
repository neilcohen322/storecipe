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

import { SortMenu } from "../SortMenu";
import type { RecipeSort } from "../../api/catalog";

const options: { value: RecipeSort; label: string }[] = [
  { value: "updatedAt:desc", label: "Recently updated" },
  { value: "title:asc", label: "Title A to Z" },
];

test("sort menu is a labelled modal", async () => {
  const screen = await render(
    <SortMenu visible value="updatedAt:desc" options={options} onSelect={jest.fn()} onDismiss={jest.fn()} />,
  );
  const panel = screen.getByTestId("sort-menu-panel", { includeHiddenElements: true });
  expect(panel.props.role).toBe("dialog");
  expect(panel.props["aria-modal"]).toBe(true);
  expect(panel.props["aria-labelledby"]).toBe("sort-menu-title");
});

test("one click selects a sort and dismisses", async () => {
  const onSelect = jest.fn();
  const onDismiss = jest.fn();
  const screen = await render(
    <SortMenu visible value="updatedAt:desc" options={options} onSelect={onSelect} onDismiss={onDismiss} />,
  );
  fireEvent.press(screen.getByRole("button", { name: "Title A to Z", includeHiddenElements: true }));
  expect(onSelect).toHaveBeenCalledTimes(1);
  expect(onSelect).toHaveBeenCalledWith("title:asc");
  expect(onDismiss).toHaveBeenCalledTimes(1);
});

test("Escape, Back, and backdrop dismiss without selecting", async () => {
  const onSelect = jest.fn();
  const onDismiss = jest.fn();
  const screen = await render(
    <SortMenu visible value="updatedAt:desc" options={options} onSelect={onSelect} onDismiss={onDismiss} />,
  );
  screen.getByTestId("sort-menu").props.onRequestClose();
  fireEvent.press(screen.getByTestId("sort-menu-backdrop", { includeHiddenElements: true }));
  expect(onDismiss).toHaveBeenCalledTimes(2);
  expect(onSelect).not.toHaveBeenCalled();
});

test("moves focus into the menu and restores it on close", async () => {
  const initialFocus = jest.fn();
  const returnFocus = jest.fn();
  const screen = await render(
    <SortMenu
      visible
      value="updatedAt:desc"
      options={options}
      onSelect={jest.fn()}
      onDismiss={jest.fn()}
      initialFocusRef={{ current: { focus: initialFocus } }}
      returnFocusRef={{ current: { focus: returnFocus } }}
    />,
  );
  expect(initialFocus).toHaveBeenCalledTimes(1);
  await screen.rerender(
    <SortMenu
      visible={false}
      value="updatedAt:desc"
      options={options}
      onSelect={jest.fn()}
      onDismiss={jest.fn()}
      initialFocusRef={{ current: { focus: initialFocus } }}
      returnFocusRef={{ current: { focus: returnFocus } }}
    />,
  );
  expect(returnFocus).toHaveBeenCalledTimes(1);
});
