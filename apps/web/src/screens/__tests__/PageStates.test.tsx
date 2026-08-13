import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

import NotFoundRoute from "../../../app/+not-found";
import type { createIngestionApi } from "../../api/ingestion";
import { ImportSessionProvider } from "../../imports/ImportSessionProvider";
import { ImportScreen } from "../ImportScreen";
import { LandingScreen } from "../LandingScreen";
import { RecipeListScreen } from "../RecipeListScreen";

const mockReplace = jest.fn();
const mockPush = jest.fn();
const mockUseAuth = jest.fn();

jest.mock("expo-router", () => {
  const { useEffect } = require("react");
  return {
    useLocalSearchParams: () => ({}),
    useRouter: () => ({ push: mockPush, replace: mockReplace }),
    useFocusEffect: (callback: () => void | (() => void)) => {
      useEffect(() => {
        const cleanup = callback();
        return typeof cleanup === "function" ? cleanup : undefined;
      }, [callback]);
    },
  };
});
jest.mock("../../auth/AuthProvider", () => ({
  useAuth: () => mockUseAuth(),
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({
    theme: {
      colors: { canvas: "#fff", surface: "#fff", elevatedSurface: "#fff", text: "#111", mutedText: "#555", border: "#ddd", accent: "#286", accentHover: "#174", accentContrast: "#fff", danger: "#b42318", warning: "#850", success: "#286", focusRing: "#286", scrim: "rgba(0,0,0,.4)" },
      spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, "2xl": 48 },
      sizing: { control: 44, icon: 24, touchTarget: 48 },
      radii: { sm: 8, md: 12, lg: 16, pill: 999 },
      type: { caption: 12, body: 15, subtitle: 18, heading: 28, display: 54 },
    },
  }),
}));
jest.mock("react-native-safe-area-context", () => ({ useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }) }));

const forbiddenCopy = /https?:\/\/|bearer\s|access[_ -]?token|<\/?[a-z][^>]*>|error_category|provider[_ -]?(payload|response)|exception:/i;

beforeEach(() => jest.clearAllMocks());

test("renders landing auth loading and a live, sanitized login error", async () => {
  const rawError = "https://provider.example/callback access_token=secret";
  const screen = await render(<LandingScreen authPresentation="auth0" authConfigured isLoading isAuthenticated={false} onLogin={jest.fn()} onContinue={jest.fn()} />);

  expect(screen.getByText("Checking session…")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();

  await screen.rerender(<LandingScreen authPresentation="auth0" authConfigured isLoading={false} isAuthenticated={false} errorMessage={rawError} onLogin={jest.fn()} onContinue={jest.fn()} />);
  const safeError = screen.getByText("We couldn't sign you in. Please try again.");
  expect(safeError.props.accessibilityRole).toBe("alert");
  expect(safeError.props.accessibilityLiveRegion).toBe("assertive");
  expect(String(safeError.props.children)).not.toMatch(forbiddenCopy);
  expect(screen.queryByText(rawError)).toBeNull();
});

test("renders recipe loading, safe retry, and semantic empty states from real requests", async () => {
  const rawError = "https://provider.example/private access_token=secret";
  let rejectInitial!: (reason: unknown) => void;
  const initialRequest = new Promise<never>((_resolve, reject) => { rejectInitial = reject; });
  const listRecipes = jest.fn().mockReturnValueOnce(initialRequest).mockResolvedValueOnce({ items: [], nextCursor: null });
  const catalog = {
    listRecipes,
    listRecipeFacets: jest.fn().mockResolvedValue({
      ingredients: [],
      ingredientNextCursor: null,
      tags: [],
      tagNextCursor: null,
      totalMinutes: null,
      rating: { min: 1, max: 5 },
      ratingState: ["any", "rated", "unrated"],
      sort: { unconditional: [], requiresAvailableIngredient: [], requiresPreferredTag: [] },
    }),
    resolveRecipeFacetSelections: jest.fn().mockResolvedValue({ ingredients: [], tags: [] }),
  } as unknown as React.ComponentProps<typeof RecipeListScreen>["catalog"];
  const screen = await render(<RecipeListScreen catalog={catalog} onOpenDetail={jest.fn()} onCreate={jest.fn()} onImport={jest.fn()} onLogout={jest.fn()} onUnauthorized={jest.fn()} />);

  expect(screen.getAllByTestId("recipe-card-skeleton")).toHaveLength(3);
  await act(async () => rejectInitial(new Error(rawError)));
  const safeError = await screen.findByText("We couldn't load your recipes. Please try again.");
  expect(String(safeError.props.children)).not.toMatch(forbiddenCopy);
  expect(screen.queryByText(rawError)).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByLabelText("Empty state")).toBeTruthy());
  expect(screen.getByRole("header", { name: "Your recipe library is empty." })).toBeTruthy();
});

test("renders import validation and terminal status with live semantics and safe copy", async () => {
  const ingestion = {
    createUrlImport: jest.fn().mockResolvedValue({ jobId: "job-1" }),
    createTextImport: jest.fn(),
    getImport: jest.fn().mockResolvedValue({ id: "job-1", status: "timed_out", attemptCount: 1, createdRecipeId: null, errorCategory: "import_deadline_exceeded", cancellationRequested: false }),
  } as unknown as ReturnType<typeof createIngestionApi>;
  const screen = await render(<ImportSessionProvider ingestion={ingestion} onUnauthorized={jest.fn()}><ImportScreen onBack={jest.fn()} /></ImportSessionProvider>);

  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));
  const validation = screen.getByTestId("inline-notice");
  expect(validation.props.accessibilityLiveRegion).toBe("assertive");

  await fireEvent.changeText(screen.getByLabelText("Recipe URL"), "https://example.com/soup");
  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));
  const terminal = await screen.findByText("This import took too long and stopped.");
  expect(terminal.parent?.props.accessibilityLiveRegion).toBe("polite");
  expect(String(terminal.props.children)).not.toMatch(forbiddenCopy);
  expect(screen.queryByText("import_deadline_exceeded")).toBeNull();
  expect(screen.getByRole("button", { name: "Retry import" })).toBeTruthy();
});

test("waits for authentication before offering an auth-aware not-found action", async () => {
  mockUseAuth.mockReturnValue({ isAuthenticated: false, isLoading: true });
  const screen = await render(<NotFoundRoute />);

  expect(screen.getByLabelText("Checking session").props.accessibilityRole).toBe("progressbar");
  expect(screen.queryByRole("button", { name: /Return to/ })).toBeNull();
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
