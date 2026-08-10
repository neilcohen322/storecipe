import React from "react";
import { Text, TextInput } from "react-native";
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
    expect(screen.props.contentContainerStyle).toEqual(expect.arrayContaining([expect.objectContaining({ paddingTop: 26, paddingRight: 20, paddingBottom: 28, paddingLeft: 22 })]));
    expect(getByTestId("screen-content", { includeHiddenElements: true }).props.style).toEqual(expect.arrayContaining([expect.objectContaining({ maxWidth: 1120 })]));
    expect(getByRole("button", { name: "Save", includeHiddenElements: true }).props.style).toEqual(expect.arrayContaining([expect.objectContaining({ minHeight: 44 })]));
  });

  it("only exposes the focus ring while focused and keeps skeleton static for reduced motion", async () => {
    const { getByRole, getByTestId } = await renderWithTheme(<><Button label="Focus me" /><Skeleton /></>);
    const button = getByRole("button", { name: "Focus me", includeHiddenElements: true });
    expect(button.props.style.some((style: { outlineWidth?: number }) => style?.outlineWidth)).toBe(false);
    await fireEvent(button, "focus");
    expect(getByRole("button", { name: "Focus me", includeHiddenElements: true }).props.style.some((style: { outlineWidth?: number }) => style?.outlineWidth === 2)).toBe(true);
    expect(getByTestId("skeleton").props.style).not.toHaveProperty("animationDuration");
  });

  it("gives confirmation dialogs modal semantics and a close path", async () => {
    const { getByTestId } = await renderWithTheme(<ConfirmDialog visible title="Delete recipe?" onConfirm={() => undefined} onCancel={() => undefined} />);
    const dialog = getByTestId("confirm-dialog-panel", { includeHiddenElements: true });
    expect(dialog.props.accessibilityRole).toBe("alert");
    expect(dialog.props["aria-modal"]).toBe(true);
    expect(dialog.props["aria-labelledby"]).toBe("confirm-dialog-title");
  });
});
