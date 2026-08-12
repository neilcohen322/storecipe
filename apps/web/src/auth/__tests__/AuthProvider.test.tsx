import type { ReactElement } from "react";
import { fireEvent, render } from "@testing-library/react-native";
import { Platform, Pressable, Text } from "react-native";

import { AuthProvider, useAuth } from "../AuthProvider";

jest.mock("react-native-auth0", () => ({
  Auth0Provider: ({ children }: { children: React.ReactNode }) => children,
  useAuth0: jest.fn(),
}));

const { useAuth0 } = jest.requireMock("react-native-auth0") as { useAuth0: jest.Mock };
const mockAuthorize = jest.fn();
const originalWindow = globalThis.window;

afterEach(() => {
  jest.restoreAllMocks();
  Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
});

function LoginButton() {
  const { login } = useAuth();
  return <Pressable accessibilityRole="button" accessibilityLabel="Sign in" onPress={() => void login()}><Text>Sign in</Text></Pressable>;
}

test("configures Auth0 for bearer tokens with refresh-token rotation", () => {
  process.env.EXPO_PUBLIC_AUTH0_DOMAIN = "tenant.auth0.com";
  process.env.EXPO_PUBLIC_AUTH0_CLIENT_ID = "client-id";
  process.env.EXPO_PUBLIC_AUTH0_AUDIENCE = "https://api.test";

  const provider = AuthProvider({
    children: null,
  }) as ReactElement<{
    useDPoP?: boolean;
    useRefreshTokens?: boolean;
    domain?: string;
    clientId?: string;
  }>;

  expect(provider.props.useDPoP).toBe(false);
  expect(provider.props.useRefreshTokens).toBe(true);
  expect(provider.props.domain).toBe("tenant.auth0.com");
  expect(provider.props.clientId).toBe("client-id");
});

test("starts Auth0-hosted Google login with the API scope and web redirect", async () => {
  process.env.EXPO_PUBLIC_AUTH0_DOMAIN = "tenant.auth0.com";
  process.env.EXPO_PUBLIC_AUTH0_CLIENT_ID = "client-id";
  process.env.EXPO_PUBLIC_AUTH0_AUDIENCE = "https://api.test";
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { origin: "https://storecipe.test" } },
  });
  jest.replaceProperty(Platform, "OS", "web");
  mockAuthorize.mockReset().mockResolvedValue(undefined);
  useAuth0.mockReturnValue({ user: null, isLoading: false, error: null, authorize: mockAuthorize, clearSession: jest.fn(), getCredentials: jest.fn() });

  const screen = await render(<AuthProvider><LoginButton /></AuthProvider>);
  fireEvent.press(screen.getByRole("button", { name: "Sign in" }));

  expect(mockAuthorize).toHaveBeenCalledWith({
    audience: "https://api.test",
    connection: "google-oauth2",
    redirectUrl: "https://storecipe.test",
    scope: "openid profile email offline_access recipes:read recipes:write ratings:write",
  });
});
