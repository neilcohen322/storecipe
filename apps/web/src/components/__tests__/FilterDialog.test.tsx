import React from "react";
import { StyleSheet, Text } from "react-native";
import { act, fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 10, right: 4, bottom: 12, left: 6 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

import { FilterDialog, type FilterDraft } from "../FilterDialog";

const draft: FilterDraft = { ingredient: ["egg"], tag: ["quick"], maxTotalMinutes: 30, minRating: 4, ratingState: "rated" };

test("filter dialog is a labelled modal with layout, actions, dismiss, and focus", async () => {
  const onApply = jest.fn();
  const onClear = jest.fn();
  const onDismiss = jest.fn();
  const onChange = jest.fn();
  const initialFocus = jest.fn();
  const returnFocus = jest.fn();
  const initialFocusRef = { current: { focus: initialFocus } };
  const returnFocusRef = { current: { focus: returnFocus } };

  const screen = await render(
    <FilterDialog
      visible
      layoutMode="expanded"
      draft={draft}
      onChange={onChange}
      onApply={onApply}
      onClear={onClear}
      onDismiss={onDismiss}
      initialFocusRef={initialFocusRef}
      returnFocusRef={returnFocusRef}
    >
      <Text>Bound controls</Text>
    </FilterDialog>,
  );

  const dialog = screen.getByTestId("filter-dialog", { includeHiddenElements: true });
  const desktopPanel = screen.getByTestId("filter-dialog-panel", { includeHiddenElements: true });
  const backdrop = screen.getByTestId("filter-dialog-backdrop", { includeHiddenElements: true });
  expect(dialog.props.accessibilityLabel).toBe("Filters");
  expect(dialog.props["aria-labelledby"]).toBe("filter-dialog-title");
  expect(desktopPanel.props.role).not.toBe("dialog");
  expect(desktopPanel.props["aria-modal"]).toBeUndefined();
  expect(desktopPanel.props.tabIndex).toBe(-1);
  expect(backdrop.props.accessible).toBe(false);
  expect(backdrop.props.focusable).toBe(false);
  expect(StyleSheet.flatten(desktopPanel.props.style).maxWidth).toBe(720);
  expect(initialFocus).toHaveBeenCalledTimes(1);

  await screen.rerender(
    <FilterDialog
      visible
      layoutMode="compact"
      draft={draft}
      onChange={onChange}
      onApply={onApply}
      onClear={onClear}
      onDismiss={onDismiss}
      initialFocusRef={initialFocusRef}
      returnFocusRef={returnFocusRef}
    >
      <Text>Bound controls</Text>
    </FilterDialog>,
  );
  const compactStyle = StyleSheet.flatten(screen.getByTestId("filter-dialog-panel", { includeHiddenElements: true }).props.style);
  expect(compactStyle.flex).toBe(1);
  expect(compactStyle.maxWidth).toBeUndefined();
  expect(compactStyle.paddingTop).toBe(10);
  expect(compactStyle.paddingRight).toBe(4);
  expect(compactStyle.paddingBottom).toBe(12);
  expect(compactStyle.paddingLeft).toBe(6);

  await fireEvent.press(screen.getByLabelText("Apply", { includeHiddenElements: true }));
  await fireEvent.press(screen.getByLabelText("Clear", { includeHiddenElements: true }));
  expect(onApply).toHaveBeenCalledTimes(1);
  expect(onClear).toHaveBeenCalledTimes(1);
  expect(onDismiss).not.toHaveBeenCalled();
  onApply.mockClear();

  await act(async () => {
    screen.getByTestId("filter-dialog").props.onRequestClose();
  });
  await fireEvent.press(screen.getByTestId("filter-dialog-backdrop", { includeHiddenElements: true }));
  await fireEvent.press(screen.getByRole("button", { name: "Cancel", includeHiddenElements: true }));
  expect(onDismiss).toHaveBeenCalledTimes(3);
  expect(onApply).not.toHaveBeenCalled();

  await screen.rerender(
    <FilterDialog
      visible={false}
      layoutMode="expanded"
      draft={draft}
      onChange={onChange}
      onApply={onApply}
      onClear={onClear}
      onDismiss={onDismiss}
      initialFocusRef={initialFocusRef}
      returnFocusRef={returnFocusRef}
    >
      <Text>Bound controls</Text>
    </FilterDialog>,
  );
  expect(returnFocus).toHaveBeenCalledTimes(1);
});
