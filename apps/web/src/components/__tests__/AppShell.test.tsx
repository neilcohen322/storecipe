import { fireEvent, render, waitFor, within } from "@testing-library/react-native";
import { Text } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AppShell, getLayoutMode } from "../AppShell";
import { ThemeProvider } from "../../theme/ThemeProvider";

jest.mock("expo-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => children,
  usePathname: () => "/recipes",
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));

const renderShell = (width: number) => render(
  <ThemeProvider systemSchemeOverride="light">
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width, height: 800 }, insets: { top: 0, right: 0, bottom: 0, left: 0 } }}>
      <AppShell viewportWidth={width}><Text>Page content</Text></AppShell>
    </SafeAreaProvider>
  </ThemeProvider>,
);

describe("AppShell", () => {
  it.each([[390, "compact"], [768, "medium"], [1440, "expanded"]] as const)("uses %s as %s layout", (width, mode) => {
    expect(getLayoutMode(width)).toBe(mode);
  });

  it("renders compact Create as a primary route destination", async () => {
    const { getByRole, queryByTestId } = await renderShell(390);
    expect(getByRole("link", { name: "Create" })).toBeTruthy();
    expect(queryByTestId("page-create-action")).toBeNull();
  });

  it.each([768, 1440])("suppresses Create navigation and shows the desktop page action at %s", async (width) => {
    const { getByTestId } = await renderShell(width);
    expect(within(getByTestId("desktop-sidebar")).queryByRole("link", { name: "Create" })).toBeNull();
    expect(getByTestId("page-create-action")).toBeTruthy();
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
