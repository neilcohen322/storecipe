import { fireEvent, render } from "@testing-library/react-native";

import IndexRoute from "../../../app/index";
import { authPresentation as productionAuthPresentation } from "../../app/ProductionAuthProvider";
import { returnPathStorage } from "../../auth/returnPathStorage";
import { LandingScreen } from "../LandingScreen";
import { authPresentation as fixtureAuthPresentation } from "../../testing/E2EAuthProvider";
import { ThemeProvider } from "../../theme/ThemeProvider";

const mockRootRedirect = jest.fn();
const mockReplace = jest.fn();

jest.mock("expo-router", () => ({
  Redirect: ({ href }: { href: string }) => {
    mockRootRedirect(href);
    return null;
  },
  useRouter: () => ({ replace: mockReplace }),
}));

jest.mock("../../auth/AuthProvider", () => ({
  useAuth: jest.fn(),
}));

const { useAuth } = jest.requireMock("../../auth/AuthProvider") as { useAuth: jest.Mock };

function renderWithTheme(ui: React.ReactElement) {
  return render(<ThemeProvider systemSchemeOverride="light">{ui}</ThemeProvider>);
}

beforeEach(() => {
  mockRootRedirect.mockReset();
  mockReplace.mockReset();
  returnPathStorage.clear();
});

test("presents neutral Auth0 sign-in alongside the local recipe preview", async () => {
  const onLogin = jest.fn();
  const screen = await renderWithTheme(
    <LandingScreen
      authPresentation={productionAuthPresentation}
      authConfigured
      isLoading={false}
      isAuthenticated={false}
      onLogin={onLogin}
      onContinue={jest.fn()}
    />,
  );

  fireEvent.press(screen.getByRole("button", { name: "Sign in" }));

  expect(onLogin).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Your recipes, gathered in one calm place.")).toBeTruthy();
  expect(screen.getByLabelText("Recipe library preview")).toBeTruthy();
  expect(screen.getByText("Authentication is handled securely.")).toBeTruthy();
  expect(screen.queryByText(/Google/i)).toBeNull();
});

test("presents the fixture authentication as an explicit demo", async () => {
  const screen = await renderWithTheme(
    <LandingScreen
      authPresentation={fixtureAuthPresentation}
      authConfigured
      isLoading={false}
      isAuthenticated={false}
      onLogin={jest.fn()}
      onContinue={jest.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "Explore demo" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
});

test("keeps a cancelled login on the landing view", async () => {
  const screen = await renderWithTheme(
    <LandingScreen
      authPresentation={productionAuthPresentation}
      authConfigured
      isLoading={false}
      isAuthenticated={false}
      errorMessage="Login cancelled"
      onLogin={jest.fn()}
      onContinue={jest.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
  expect(screen.getByText("We couldn't sign you in. Please try again.")).toBeTruthy();
  expect(screen.queryByText("Login cancelled")).toBeNull();
});

test("redirects an authenticated root visit to recipes when a return path is saved", async () => {
  mockRootRedirect.mockReset();
  returnPathStorage.save("/recipes");
  useAuth.mockReturnValue({
    isLoading: false,
    isAuthenticated: true,
    errorMessage: null,
    login: jest.fn(),
  });

  await renderWithTheme(<IndexRoute />);

  expect(mockRootRedirect).toHaveBeenCalledWith("/recipes");
});

test("does not bounce an authenticated root visit back to recipes when no return path is saved", async () => {
  mockRootRedirect.mockReset();
  returnPathStorage.clear();
  useAuth.mockReturnValue({
    isLoading: false,
    isAuthenticated: true,
    errorMessage: null,
    login: jest.fn(),
  });

  const screen = await renderWithTheme(<IndexRoute />);

  expect(mockRootRedirect).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Continue to recipes" })).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Continue to recipes" }));
  expect(mockReplace).toHaveBeenCalledWith("/recipes");
});

test("saves recipes as the return path before starting sign-in from the root landing", async () => {
  const login = jest.fn().mockResolvedValue(undefined);
  useAuth.mockReturnValue({
    isLoading: false,
    isAuthenticated: false,
    errorMessage: null,
    login,
  });

  const screen = await renderWithTheme(<IndexRoute />);
  await fireEvent.press(screen.getByRole("button", { name: "Sign in" }));

  expect(returnPathStorage.peek()).toBe("/recipes");
  expect(login).toHaveBeenCalledTimes(1);
});
