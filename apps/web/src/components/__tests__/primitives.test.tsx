import React from "react";
import { StyleSheet, Text, TextInput, type PressableStateCallbackType } from "react-native";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 10, right: 4, bottom: 12, left: 6 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
  useTheme: () => ({ theme: { colors: { canvas: "#f7fff9", surface: "#fff", elevatedSurface: "#fff", text: "#10231c", mutedText: "#527060", border: "#d0e5d6", accent: "#2d6a4f", accentHover: "#1b4332", accentContrast: "#fff", success: "#2d6a4f", warning: "#b7791f", danger: "#b42318", focusRing: "#40916c", scrim: "rgba(0,0,0,.4)" }, spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, "2xl": 48 }, sizing: { control: 44, icon: 24, touchTarget: 48 }, radii: { sm: 8, md: 12, lg: 16, pill: 999 }, type: { caption: 12, body: 15, subtitle: 18, heading: 28, display: 54 } } }),
}));
import { ThemeProvider } from "../../theme/ThemeProvider";
import {
  Button, ConfirmDialog, EmptyState, ErrorState, Field, ImportProgress, InlineNotice,
  LoadingState, OfflineBanner, PageHeader, RatingControl, RecipeMedia, ResponsiveGrid,
  Screen, Section, Skeleton, StatusBadge, TextArea, Toast,
} from "../index";

const renderWithTheme = (ui: React.ReactElement) => render(<ThemeProvider systemSchemeOverride="light">{ui}</ThemeProvider>);

describe("accessible token-driven primitives", () => {
  it("renders named button variants with a 44px target and loading disables action", async () => {
    const onPress = jest.fn();
    const { getByRole } = await renderWithTheme(<Button label="Save recipe" loading onPress={onPress} />);
    const button = getByRole("button", { name: "Save recipe" });
    expect(button.props.accessibilityState).toMatchObject({ disabled: true, busy: true });
    expect(button.props.style).toEqual(expect.arrayContaining([expect.objectContaining({ minHeight: 44 })]));
    button.props.onPress?.();
    expect(onPress).not.toHaveBeenCalled();
  });

  it("accepts function styles typed against the complete pressable state", async () => {
    const states: PressableStateCallbackType[] = [];
    const style = (state: PressableStateCallbackType) => {
      states.push(state);
      return { opacity: state.pressed ? 0.5 : 1 };
    };
    const { getByRole } = await renderWithTheme(<Button label="Stateful" style={style} />);

    expect(states).toEqual([expect.objectContaining({ pressed: false })]);
    expect(StyleSheet.flatten(getByRole("button", { name: "Stateful" }).props.style).opacity).toBe(1);
  });

  it("associates field labels, hints, errors, and textarea controls", async () => {
    const { getByLabelText, getByText } = await renderWithTheme(<>
      <Field label="Recipe title" hint="Keep it short" error="Title is required" control={<TextInput />} />
      <TextArea label="Notes" value="hello" onChangeText={() => undefined} />
    </>);
    expect(getByLabelText("Recipe title").props.accessibilityState).toMatchObject({ invalid: true });
    expect(getByText("Title is required")).toBeTruthy();
    expect(getByLabelText("Notes")).toBeTruthy();
  });

  it("provides layout, status, and deterministic recipe media primitives", async () => {
    const { getByTestId } = await renderWithTheme(<>
      <Screen><PageHeader title="Recipes" /><Section title="Latest"><ResponsiveGrid><Text>Card</Text></ResponsiveGrid></Section></Screen>
      <InlineNotice message="Saved" /><EmptyState title="Nothing here" /><LoadingState label="Loading recipes" />
      <ErrorState title="Failed" /><OfflineBanner /><StatusBadge status="success" /><ImportProgress status="review_required" />
      <RecipeMedia title="Pasta" tags={["Italian"]} /><Skeleton /><Toast message="Saved" visible /><ConfirmDialog visible title="Delete?" onConfirm={() => undefined} onCancel={() => undefined} />
    </>);
    expect(getByTestId("import-progress", { includeHiddenElements: true })).toBeTruthy();
    expect(getByTestId("recipe-media", { includeHiddenElements: true }).props.accessibilityLabel).toMatch(/Pasta/);
  });

  it("keeps section headings outside semantic lists", async () => {
    const { getByLabelText } = await renderWithTheme(
      <Section title="Ingredients" accessibilityRole="list" accessibilityLabel="Ingredients">
        <Text accessibilityRole="text">Tomato</Text>
      </Section>,
    );
    const list = getByLabelText("Ingredients");
    expect(list.props.children).not.toContainEqual(expect.objectContaining({ props: expect.objectContaining({ accessibilityRole: "header" }) }));
  });

  it("keeps import progress coarse and exposes rating controls by name", async () => {
    const onChange = jest.fn();
    const { queryByText, getAllByRole } = await renderWithTheme(<><ImportProgress status="processing" /><RatingControl value={3} onChange={onChange} /></>);
    expect(queryByText(/fetching|rendering|extracting|saving/i)).toBeNull();
    expect(getAllByRole("button")).toHaveLength(5);
  });

  it("maps every public import status without internal stage labels", async () => {
    const statuses = ["queued", "processing", "completed", "review_required", "failed", "cancelled", "timed_out"] as const;
    for (const status of statuses) {
      const { getByTestId, unmount } = await renderWithTheme(<ImportProgress status={status} />);
      const progress = getByTestId("import-progress", { includeHiddenElements: true });
      expect(progress.props.accessibilityLabel).toMatch(/^Import status:/);
      expect(progress.props.accessibilityLabel).not.toMatch(/fetching|rendering|extracting|saving/i);
      await unmount();
    }
  });

  it("owns safe-area padding, readable width, and 44px interactive targets", async () => {
    const { getByTestId, getByRole } = await renderWithTheme(<>
      <Screen testID="screen"><Button label="Save" /><RatingControl value={2} onChange={() => undefined} /><StatusBadge status="success" /><InlineNotice message="Notice" /><Toast message="Toast" /></Screen>
    </>);
    const screen = getByTestId("screen", { includeHiddenElements: true });
    expect(StyleSheet.flatten(screen.props.style)).toEqual(expect.objectContaining({ flex: 1 }));
    expect(screen.props.contentContainerStyle).toEqual(expect.arrayContaining([expect.objectContaining({ paddingTop: 26, paddingRight: 20, paddingBottom: 28, paddingLeft: 22 })]));
    expect(getByTestId("screen-content", { includeHiddenElements: true }).props.style).toEqual(expect.arrayContaining([expect.objectContaining({ maxWidth: 1120, width: "100%" })]));
    expect(getByRole("button", { name: "Save", includeHiddenElements: true }).props.style).toEqual(expect.arrayContaining([expect.objectContaining({ minHeight: 44 })]));
  });

  it("keeps grid items shrinking inside the viewport instead of stretching off-screen", async () => {
    const { getAllByTestId } = await renderWithTheme(
      <ResponsiveGrid testID="grid"><Text>One</Text><Text>Two</Text><Text>Three</Text></ResponsiveGrid>,
    );
    for (const item of getAllByTestId("responsive-grid-item")) {
      expect(StyleSheet.flatten(item.props.style)).toEqual(expect.objectContaining({
        flexGrow: 1,
        flexShrink: 1,
        flexBasis: 260,
        maxWidth: "100%",
      }));
    }
  });

  it("renders error states as contained alerts with description and retry, not color alone", async () => {
    const { getByRole, getByText, getByTestId } = await renderWithTheme(
      <ErrorState title="We couldn't load your recipes." description="Please try again." action={<Button label="Try again" onPress={() => undefined} />} />,
    );
    const alert = getByTestId("error-state");
    const style = StyleSheet.flatten(alert.props.style);
    expect(alert.props.accessibilityRole).toBe("alert");
    expect(getByText("Error")).toBeTruthy();
    expect(getByText("Please try again.")).toBeTruthy();
    expect(getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(style.borderWidth).toBeGreaterThanOrEqual(1);
    expect(style.backgroundColor).toBe("#fff");
    expect(style.width).toBe("100%");
  });

  it("does not paint secondary buttons with the accent hover color", async () => {
    const { getByRole } = await renderWithTheme(<Button label="List view" variant="secondary" />);
    const button = getByRole("button", { name: "List view" });
    await fireEvent(button, "hoverIn");
    expect(StyleSheet.flatten(button.props.style).backgroundColor).toBe("#fff");
  });

  it("shows focus rings for keyboard focus but not pointer focus", async () => {
    const { getByRole } = await renderWithTheme(<><Button label="Focus me" /><RatingControl value={2} onChange={() => undefined} /></>);
    const button = getByRole("button", { name: "Focus me", includeHiddenElements: true });
    await fireEvent(button, "pointerDown");
    await fireEvent(button, "focus", { currentTarget: { matches: () => false } });
    expect(StyleSheet.flatten(button.props.style).outlineWidth).toBeUndefined();
    await fireEvent(button, "blur");
    await fireEvent(button, "focus", { currentTarget: { matches: (selector: string) => selector === ":focus-visible" } });
    expect(StyleSheet.flatten(button.props.style).outlineWidth).toBe(2);

    const rating = getByRole("button", { name: "Rate 3 out of 5", includeHiddenElements: true });
    await fireEvent(rating, "pointerDown");
    await fireEvent(rating, "focus", { currentTarget: { matches: () => false } });
    expect(StyleSheet.flatten(rating.props.style).outlineWidth).toBeUndefined();
    await fireEvent(rating, "blur");
    await fireEvent(rating, "focus", { currentTarget: { matches: (selector: string) => selector === ":focus-visible" } });
    expect(StyleSheet.flatten(rating.props.style).outlineWidth).toBe(2);
  });

  it("keeps skeleton rendering static", async () => {
    const { getByTestId } = await renderWithTheme(<Skeleton />);
    expect(getByTestId("skeleton").props.style).not.toHaveProperty("animationDuration");
  });

  it("gives confirmation dialogs web dialog semantics and a close path", async () => {
    const { getByTestId } = await renderWithTheme(<ConfirmDialog visible title="Delete recipe?" onConfirm={() => undefined} onCancel={() => undefined} />);
    const dialog = getByTestId("confirm-dialog-panel", { includeHiddenElements: true });
    expect(dialog.props.role).toBe("dialog");
    expect(dialog.props.accessibilityRole).not.toBe("alert");
    expect(dialog.props["aria-modal"]).toBe(true);
    expect(dialog.props["aria-labelledby"]).toBe("confirm-dialog-title");
  });

  it("keeps every interactive primitive at least 44 by 44", async () => {
    const { getAllByRole, getByLabelText } = await renderWithTheme(<>
      {(["primary", "secondary", "quiet", "icon", "danger"] as const).map((variant) => <Button key={variant} label={`${variant} action`} variant={variant} />)}
      <Field label="Single line" control={<TextInput />} />
      <TextArea label="Long form" />
      <RatingControl value={2} onChange={() => undefined} />
      <ConfirmDialog visible title="Confirm dimensions" onConfirm={() => undefined} onCancel={() => undefined} />
    </>);
    for (const control of getAllByRole("button", { includeHiddenElements: true })) {
      const style = StyleSheet.flatten(control.props.style);
      expect(style.minHeight).toBeGreaterThanOrEqual(44);
      expect(style.minWidth).toBeGreaterThanOrEqual(44);
    }
    for (const label of ["Single line", "Long form"]) {
      const style = StyleSheet.flatten(getByLabelText(label, { includeHiddenElements: true }).props.style);
      expect(style.minHeight).toBeGreaterThanOrEqual(44);
      expect(style.minWidth).toBeGreaterThanOrEqual(44);
    }
  });

  it("moves focus into the dialog and returns it when the dialog closes", async () => {
    const initialFocus = jest.fn();
    const returnFocus = jest.fn();
    const initialFocusRef = { current: { focus: initialFocus } };
    const returnFocusRef = { current: { focus: returnFocus } };
    const { rerender } = await renderWithTheme(<ConfirmDialog visible title="Focus lifecycle" onConfirm={() => undefined} onCancel={() => undefined} initialFocusRef={initialFocusRef} returnFocusRef={returnFocusRef} />);
    expect(initialFocus).toHaveBeenCalledTimes(1);
    await rerender(<ThemeProvider systemSchemeOverride="light"><ConfirmDialog visible={false} title="Focus lifecycle" onConfirm={() => undefined} onCancel={() => undefined} initialFocusRef={initialFocusRef} returnFocusRef={returnFocusRef} /></ThemeProvider>);
    expect(returnFocus).toHaveBeenCalledTimes(1);
  });
});
