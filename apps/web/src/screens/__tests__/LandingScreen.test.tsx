import { fireEvent, render } from "@testing-library/react-native";

import IndexRoute from "../../../app/index";
import { LandingScreen } from "../LandingScreen";
import { ThemeProvider } from "../../theme/ThemeProvider";

const mockRootRedirect = jest.fn();

jest.mock("expo-router", () => ({
  Redirect: ({ href }: { href: string }) => {
    mockRootRedirect(href);
    return null;
  },
  useRouter: () => ({ replace: jest.fn() }),
}));

jest.mock("../../auth/AuthProvider", () => ({
  useAuth: jest.fn(),
}));

const { useAuth } = jest.requireMock("../../auth/AuthProvider") as { useAuth: jest.Mock };

function renderWithTheme(ui: React.ReactElement) {
  return render(<ThemeProvider systemSchemeOverride="light">{ui}</ThemeProvider>);
}

test("presents one Google sign-in action alongside the local recipe preview", async () => {
  const onLogin = jest.fn();
  const screen = await renderWithTheme(
    <LandingScreen
      authConfigured
      isLoading={false}
      isAuthenticated={false}
      onLogin={onLogin}
      onContinue={jest.fn()}
    />,
  );

  fireEvent.press(screen.getByRole("button", { name: "Continue with Google" }));

  expect(onLogin).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Your recipes, gathered in one calm place.")).toBeTruthy();
  expect(screen.getByLabelText("Recipe library preview")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Log in" })).toBeNull();
});

test("keeps a cancelled login on the landing view", async () => {
  const screen = await renderWithTheme(
    <LandingScreen
      authConfigured
      isLoading={false}
      isAuthenticated={false}
      errorMessage="Login cancelled"
      onLogin={jest.fn()}
      onContinue={jest.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "Continue with Google" })).toBeTruthy();
  expect(screen.getByText("Login cancelled")).toBeTruthy();
});

test("redirects an authenticated root visit to recipes", async () => {
  mockRootRedirect.mockReset();
  useAuth.mockReturnValue({
    isLoading: false,
    isAuthenticated: true,
    errorMessage: null,
    login: jest.fn(),
  });

  await renderWithTheme(<IndexRoute />);

  expect(mockRootRedirect).toHaveBeenCalledWith("/recipes");
});
