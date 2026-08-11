import { fireEvent, render } from "@testing-library/react-native";

import { AuthGate } from "../AuthGate";
import { createReturnPathStorage } from "../returnPathStorage";

const mockLogin = jest.fn();
const mockLogout = jest.fn();
let mockPathname = "/recipes/recipe-42";
const mockRedirect = jest.fn();

jest.mock("expo-router", () => ({
  Redirect: ({ href }: { href: string }) => {
    mockRedirect(href);
    return null;
  },
  usePathname: () => mockPathname,
  useRouter: () => ({ replace: jest.fn() }),
}));

jest.mock("../AuthProvider", () => ({
  useAuth: jest.fn(),
}));

jest.mock("../../screens/LandingScreen", () => {
  const { Pressable, Text } = require("react-native");
  return {
    LandingScreen: ({ onLogin }: { onLogin(): void }) => (
      <Pressable accessibilityRole="button" accessibilityLabel="Continue with Google" onPress={onLogin}>
        <Text>Continue with Google</Text>
      </Pressable>
    ),
  };
});

const { useAuth } = jest.requireMock("../AuthProvider") as { useAuth: jest.Mock };

function authenticated() {
  useAuth.mockReturnValue({
    isLoading: false,
    isAuthenticated: true,
    errorMessage: null,
    login: mockLogin,
    logout: mockLogout,
  });
}

function unauthenticated(errorMessage: string | null = null) {
  useAuth.mockReturnValue({
    isLoading: false,
    isAuthenticated: false,
    errorMessage,
    login: mockLogin,
    logout: mockLogout,
  });
}

beforeEach(() => {
  mockLogin.mockReset().mockResolvedValue(undefined);
  mockLogout.mockReset().mockResolvedValue(undefined);
  mockRedirect.mockReset();
  mockPathname = "/recipes/recipe-42";
});

test("preserves an approved unauthenticated deep link when Google login starts", async () => {
  unauthenticated();
  const backing = { value: "" };
  const storage = createReturnPathStorage({ getItem: () => backing.value || null, setItem: (_key, value) => { backing.value = value; }, removeItem: () => { backing.value = ""; } });

  const screen = await render(<AuthGate returnPathStorage={storage}><></></AuthGate>);
  fireEvent.press(screen.getByRole("button", { name: "Continue with Google" }));

  expect(storage.consume()).toBe("/recipes/recipe-42");
  expect(mockLogin).toHaveBeenCalledTimes(1);
});

test("restores a saved approved path once after authentication", async () => {
  authenticated();
  const backing = { value: "/imports/new" };
  const storage = createReturnPathStorage({ getItem: () => backing.value || null, setItem: (_key, value) => { backing.value = value; }, removeItem: () => { backing.value = ""; } });

  await render(<AuthGate returnPathStorage={storage}><></></AuthGate>);

  expect(mockRedirect).toHaveBeenCalledWith("/imports/new");
  expect(storage.consume()).toBeNull();
});

test("clears paths that could leave the approved in-app route surface", () => {
  for (const path of [
    "https://attacker.example/recipes",
    "//attacker.example/recipes",
    "/recipes\\recipe-42",
    "/recipes/%E0%A4%A",
    "/unknown",
  ]) {
    const backing = { value: path };
    const storage = createReturnPathStorage({
      getItem: () => backing.value,
      setItem: (_key, value) => { backing.value = value; },
      removeItem: () => { backing.value = ""; },
    });

    expect(storage.consume()).toBeNull();
    expect(backing.value).toBe("");
  }
});

test("leaves a transient token failure on landing without logging out", async () => {
  unauthenticated("NO_NETWORK");
  const storage = createReturnPathStorage({ getItem: jest.fn(() => null), setItem: jest.fn(), removeItem: jest.fn() });

  await render(<AuthGate returnPathStorage={storage}><></></AuthGate>);

  expect(mockLogout).not.toHaveBeenCalled();
  expect(mockRedirect).not.toHaveBeenCalled();
});
