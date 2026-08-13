import { fireEvent, render, waitFor, within } from "@testing-library/react-native";
import { StyleSheet, Text } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AppShell, getLayoutMode } from "../AppShell";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { CreateRecipeScreen } from "../../screens/CreateRecipeScreen";

let mockPathname = "/recipes";
jest.mock("expo-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => children,
  usePathname: () => mockPathname,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

const renderShell = (width: number, bottomInset = 0) => render(
  <ThemeProvider systemSchemeOverride="light">
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width, height: 800 }, insets: { top: 0, right: 0, bottom: bottomInset, left: 0 } }}>
      <AppShell viewportWidth={width}><Text>Page content</Text></AppShell>
    </SafeAreaProvider>
  </ThemeProvider>,
);

const creationCatalog = { createRecipe: jest.fn() } as unknown as React.ComponentProps<typeof CreateRecipeScreen>["catalog"];
const renderCreateRoute = (width: number) => render(
  <ThemeProvider systemSchemeOverride="light">
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width, height: 800 }, insets: { top: 0, right: 0, bottom: 0, left: 0 } }}>
      <AppShell viewportWidth={width}>
        <CreateRecipeScreen catalog={creationCatalog} onCreated={jest.fn()} onBack={jest.fn()} onUnauthorized={jest.fn()} layoutMode={getLayoutMode(width)} />
      </AppShell>
    </SafeAreaProvider>
  </ThemeProvider>,
);

beforeEach(() => { mockPathname = "/recipes"; });

describe("AppShell", () => {
  it.each([[390, "compact"], [768, "medium"], [1440, "expanded"]] as const)("uses %s as %s layout", (width, mode) => {
    expect(getLayoutMode(width)).toBe(mode);
  });

  it("renders compact Create as a primary route destination", async () => {
    const { getByRole, queryByTestId } = await renderShell(390);
    expect(getByRole("link", { name: "Create" })).toBeTruthy();
    expect(queryByTestId("page-create-action")).toBeNull();
  });

  it("anchors compact navigation and reserves its safe-area-aware height for content", async () => {
    const { getByTestId } = await renderShell(390, 20);
    expect(StyleSheet.flatten(getByTestId("bottom-navigation").props.style)).toMatchObject({
      position: "absolute",
      bottom: 0,
      left: 0,
      right: 0,
      paddingBottom: 20,
    });
    expect(StyleSheet.flatten(getByTestId("app-shell-compact").props.style).paddingBottom).toBe(64);
    expect(StyleSheet.flatten(getByTestId("app-shell-content").props.style)).toMatchObject({
      flex: 1,
      minHeight: 0,
      minWidth: 0,
    });
  });

  it.each([768, 1440])("suppresses Create navigation and shows the desktop page action at %s", async (width) => {
    const { getByTestId } = await renderShell(width);
    expect(within(getByTestId("desktop-sidebar")).queryByRole("link", { name: "Create" })).toBeNull();
    expect(getByTestId("page-create-action")).toBeTruthy();
  });

  it.each([[390, "compact"], [768, "medium"], [1440, "expanded"]] as const)("gives /recipes/new exactly one Create recipe primary action at %s (%s)", async (width, _mode) => {
    mockPathname = "/recipes/new";
    const { getAllByRole, queryByTestId, unmount } = await renderCreateRoute(width);
    expect(getAllByRole("button", { name: "Create recipe" })).toHaveLength(1);
    expect(queryByTestId("page-create-action")).toBeNull();
    if (width === 390) {
      expect(queryByTestId("create-recipe-sticky-submit")).toBeTruthy();
      expect(queryByTestId("create-recipe-header-submit")).toBeNull();
    } else {
      expect(queryByTestId("create-recipe-header-submit")).toBeTruthy();
      expect(queryByTestId("create-recipe-sticky-submit")).toBeNull();
    }
    await unmount();
  });

  it("announces the active route and lets desktop groups collapse", async () => {
    const { getByLabelText, getByRole } = await renderShell(768);
    expect(getByRole("link", { name: "Recipes" }).props.accessibilityState.selected).toBe(true);
    expect(getByRole("link", { name: "Recipes" }).props.accessibilityHint).toBe("Current page");
    fireEvent.press(getByLabelText("Collapse workspace navigation"));
    await waitFor(() => expect(getByLabelText("Expand workspace navigation")).toBeTruthy());
  });

  it("exposes system, light, and dark theme choices", async () => {
    const { getByRole } = await renderShell(1440);
    expect(getByRole("button", { name: "Use system theme" })).toBeTruthy();
    expect(getByRole("button", { name: "Use light theme" })).toBeTruthy();
    expect(getByRole("button", { name: "Use dark theme" })).toBeTruthy();
  });
});
