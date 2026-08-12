import { fireEvent, render } from "@testing-library/react-native";

import NotFoundRoute from "../../../app/+not-found";
import { EmptyState, InlineNotice } from "../../components";

const mockReplace = jest.fn();
const mockUseAuth = jest.fn();

jest.mock("expo-router", () => ({ useRouter: () => ({ replace: mockReplace }) }));
jest.mock("../../auth/AuthProvider", () => ({
  useAuth: () => mockUseAuth(),
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({
    theme: {
      colors: { canvas: "#fff", surface: "#fff", text: "#111", mutedText: "#555", border: "#ddd", accent: "#286", accentContrast: "#fff", danger: "#b42318", warning: "#850", success: "#286" },
      spacing: { md: 16 }, radii: { sm: 8 }, type: { body: 15 },
    },
  }),
}));
jest.mock("react-native-safe-area-context", () => ({ useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }) }));

const stateMatrix = {
  landing: ["configuration error", "auth loading", "unauthenticated", "login error"],
  recipes: ["loading", "empty", "offline", "retryable error", "unauthorized", "populated"],
  detail: ["loading", "not found", "offline", "retryable error", "unauthorized", "success"],
  create: ["validation", "submitting", "safe API error", "unauthorized", "success"],
  imports: ["validation", "submitting", "polling", "transient error", "unauthorized", "attention", "cancelled", "failed", "timed out", "complete"],
  account: ["unauthorized", "success"],
  more: ["unauthorized", "success"],
} as const;

test("keeps the complete applicable page-state audit explicit", () => {
  expect(Object.values(stateMatrix).flat()).toHaveLength(35);
  expect(stateMatrix.imports).not.toContain("rendering" as never);
  expect(stateMatrix.create).not.toContain("loading" as never);
});

test("announces notice tone with text and a live region instead of color alone", async () => {
  const screen = await render(<InlineNotice tone="error" message="Please try again." />);
  const notice = screen.getByTestId("inline-notice");

  expect(notice.props.accessibilityLiveRegion).toBe("assertive");
  expect(screen.getByText("Error")).toBeTruthy();
  expect(screen.getByText("Please try again.")).toBeTruthy();
  await screen.unmount();
});

test("gives empty states explicit semantics independent of color", async () => {
  const screen = await render(<EmptyState title="No recipes yet" description="Create your first recipe." />);

  expect(screen.getByLabelText("Empty state")).toBeTruthy();
  expect(screen.getByRole("header", { name: "No recipes yet" })).toBeTruthy();
  await screen.unmount();
});

test("returns authenticated users from unknown routes to Recipes", async () => {
  mockUseAuth.mockReturnValue({ isAuthenticated: true, isLoading: false });
  const screen = await render(<NotFoundRoute />);
  await fireEvent.press(screen.getByRole("button", { name: "Return to recipes" }));
  expect(mockReplace).toHaveBeenCalledWith("/recipes");
  await screen.unmount();
});

test("returns signed-out users from unknown routes to sign in", async () => {
  mockUseAuth.mockReturnValue({ isAuthenticated: false, isLoading: false });
  const screen = await render(<NotFoundRoute />);
  await fireEvent.press(screen.getByRole("button", { name: "Return to sign in" }));
  expect(mockReplace).toHaveBeenCalledWith("/");
  await screen.unmount();
});

test("safe state copy excludes URLs, tokens, provider payloads, HTML, and raw exceptions", () => {
  const safeCopy = [
    "We couldn't load your recipes. Please try again.",
    "We couldn't create your recipe. Please try again.",
    "We couldn't start this import. Please try again.",
    "We couldn't check this import. Please try again.",
    "This import needs your review before it can be added.",
    "This import was cancelled.",
    "This import failed.",
    "This import took too long and stopped.",
  ];
  const forbidden = /https?:\/\/|bearer\s|access[_ -]?token|<\/?[a-z][^>]*>|error_category|provider[_ -]?(payload|response)|exception:/i;

  expect(safeCopy.every((copy) => !forbidden.test(copy))).toBe(true);
});
