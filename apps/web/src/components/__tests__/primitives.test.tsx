import TestRenderer, { act } from "react-test-renderer";
import React from "react";
import { Pressable, Text, TextInput } from "react-native";

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

const renderWithTheme = (ui: React.ReactElement) => {
  let renderer!: TestRenderer.ReactTestRenderer;
  act(() => { renderer = TestRenderer.create(<ThemeProvider systemSchemeOverride="light">{ui}</ThemeProvider>); });
  return renderer;
};

describe("accessible token-driven primitives", () => {
  it("renders named button variants with a 44px target and loading disables action", () => {
    const onPress = jest.fn();
    const renderer = renderWithTheme(<Button label="Save recipe" loading onPress={onPress} />);
    const button = renderer.root.find(node => node.props.accessibilityRole === "button");
    expect(button.props.accessibilityState).toMatchObject({ disabled: true, busy: true });
    expect(button.props.style(false)).toEqual(expect.arrayContaining([expect.objectContaining({ minHeight: 44 })]));
    act(() => button.props.onPress?.());
    expect(onPress).not.toHaveBeenCalled();
  });

  it("associates field labels, hints, errors, and textarea controls", () => {
    const renderer = renderWithTheme(<>
      <Field label="Recipe title" hint="Keep it short" error="Title is required" control={<TextInput />} />
      <TextArea label="Notes" value="hello" onChangeText={() => undefined} />
    </>);
    expect(renderer.root.findAllByType(TextInput)[0].props.accessibilityState).toMatchObject({ invalid: true });
    expect(renderer.root.findAllByType(Text).some(node => node.props.children === "Title is required")).toBe(true);
    expect(renderer.root.findAllByType(TextInput)[1]).toBeTruthy();
  });

  it("provides layout, status, and deterministic recipe media primitives", () => {
    const renderer = renderWithTheme(<>
      <Screen><PageHeader title="Recipes" /><Section title="Latest"><ResponsiveGrid><Text>Card</Text></ResponsiveGrid></Section></Screen>
      <InlineNotice message="Saved" /><EmptyState title="Nothing here" /><LoadingState label="Loading recipes" />
      <ErrorState title="Failed" /><OfflineBanner /><StatusBadge status="success" /><ImportProgress status="review_required" />
      <RecipeMedia title="Pasta" tags={["Italian"]} /><Skeleton /><Toast message="Saved" visible /><ConfirmDialog visible title="Delete?" onConfirm={() => undefined} onCancel={() => undefined} />
    </>);
    expect(renderer.root.findAllByType(Text).some(node => node.props.children === "Review needed")).toBe(true);
    expect(renderer.root.find(node => node.props.testID === "recipe-media").props.accessibilityLabel).toMatch(/Pasta/);
  });

  it("keeps import progress coarse and exposes rating controls by name", () => {
    const onChange = jest.fn();
    const renderer = renderWithTheme(<><ImportProgress status="processing" /><RatingControl value={3} onChange={onChange} /></>);
    expect(renderer.root.findAllByType(Text).some(node => /fetching|rendering|extracting|saving/i.test(String(node.props.children)))).toBe(false);
    expect(renderer.root.findAll(node => node.props.accessibilityRole === "button").length).toBeGreaterThanOrEqual(5);
  });
});
