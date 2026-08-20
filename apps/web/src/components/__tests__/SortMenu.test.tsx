import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 10, right: 4, bottom: 12, left: 6 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
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
  const dialog = screen.getByTestId("sort-menu", { includeHiddenElements: true });
  const panel = screen.getByTestId("sort-menu-panel", { includeHiddenElements: true });
  const backdrop = screen.getByTestId("sort-menu-backdrop", { includeHiddenElements: true });
  expect(dialog.props.accessibilityLabel).toBe("Sort");
  expect(dialog.props["aria-labelledby"]).toBe("sort-menu-title");
  expect(panel.props.role).not.toBe("dialog");
  expect(panel.props["aria-modal"]).toBeUndefined();
  expect(backdrop.props.accessible).toBe(false);
  expect(backdrop.props.focusable).toBe(false);
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
