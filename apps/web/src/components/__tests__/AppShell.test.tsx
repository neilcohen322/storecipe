import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react-native";
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
const creationIngestion = { normalizeIngredients: jest.fn() } as unknown as React.ComponentProps<typeof CreateRecipeScreen>["ingestion"];
const renderCreateRoute = (width: number) => render(
  <ThemeProvider systemSchemeOverride="light">
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width, height: 800 }, insets: { top: 0, right: 0, bottom: 0, left: 0 } }}>
      <AppShell viewportWidth={width}>
        <CreateRecipeScreen catalog={creationCatalog} ingestion={creationIngestion} onCreated={jest.fn()} onBack={jest.fn()} onUnauthorized={jest.fn()} layoutMode={getLayoutMode(width)} />
      </AppShell>
    </SafeAreaProvider>
  </ThemeProvider>,
);

beforeEach(() => { mockPathname = "/recipes"; });
afterEach(() => { cleanup(); });

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

  it.each([[390, "compact"], [768, "medium"], [1440, "expanded"]] as const)("gives /recipes/new exactly one Review recipe primary action at %s (%s)", async (width, _mode) => {
    mockPathname = "/recipes/new";
    const { getAllByRole, queryByTestId, unmount } = await renderCreateRoute(width);
    expect(getAllByRole("button", { name: "Review recipe" })).toHaveLength(1);
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

  it.each([[390, "compact"], [768, "medium"], [1440, "expanded"]] as const)("gives /recipes/new exactly one Save recipe primary action after review at %s (%s)", async (width, _mode) => {
    mockPathname = "/recipes/new";
    const normalizeIngredients = jest.fn().mockResolvedValue({
      ingredients: [{ rawText: "water", name: "water", canonicalName: "water", quantity: null, unit: null }],
    });
    const route = await render(
      <ThemeProvider systemSchemeOverride="light">
        <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width, height: 800 }, insets: { top: 0, right: 0, bottom: 0, left: 0 } }}>
          <CreateRecipeScreen
            catalog={creationCatalog}
            ingestion={{ normalizeIngredients } as unknown as React.ComponentProps<typeof CreateRecipeScreen>["ingestion"]}
            onCreated={jest.fn()}
            onBack={jest.fn()}
            onUnauthorized={jest.fn()}
            layoutMode={getLayoutMode(width)}
          />
        </SafeAreaProvider>
      </ThemeProvider>,
    );
    await fireEvent.changeText(route.getByLabelText("Title"), "Soup");
    await fireEvent.changeText(route.getByLabelText("Ingredients"), "water");
    await fireEvent.changeText(route.getByLabelText("Instructions"), "boil");
    await waitFor(() => expect(route.getByLabelText("Title").props.value).toBe("Soup"));
    await fireEvent.press(route.getByRole("button", { name: "Review recipe" }));
    await waitFor(() => expect(route.getByRole("button", { name: "Save recipe" })).toBeTruthy());
    expect(route.getAllByRole("button", { name: "Save recipe" })).toHaveLength(1);
    if (width === 390) {
      expect(route.queryByTestId("create-recipe-sticky-submit")).toBeTruthy();
      expect(route.queryByTestId("create-recipe-header-submit")).toBeNull();
    } else {
      expect(route.queryByTestId("create-recipe-header-submit")).toBeTruthy();
      expect(route.queryByTestId("create-recipe-sticky-submit")).toBeNull();
    }
    await route.unmount();
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
