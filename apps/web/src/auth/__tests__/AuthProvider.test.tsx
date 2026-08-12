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

let readAccessToken: (() => Promise<string>) | undefined;

function TokenProbe() {
  readAccessToken = useAuth().getAccessToken;
  return null;
}

test("persists the Auth0 web session for callback recovery", () => {
  process.env.EXPO_PUBLIC_AUTH0_DOMAIN = "tenant.auth0.com";
  process.env.EXPO_PUBLIC_AUTH0_CLIENT_ID = "client-id";
  process.env.EXPO_PUBLIC_AUTH0_AUDIENCE = "https://api.test";
  jest.replaceProperty(Platform, "OS", "web");

  const provider = AuthProvider({
    children: null,
  }) as ReactElement<{
    cacheLocation?: "memory" | "localstorage";
    useDPoP?: boolean;
    useRefreshTokens?: boolean;
    domain?: string;
    clientId?: string;
  }>;

  expect(provider.props.useDPoP).toBe(false);
  expect(provider.props.useRefreshTokens).toBe(true);
  expect(provider.props.cacheLocation).toBe("localstorage");
  expect(provider.props.domain).toBe("tenant.auth0.com");
  expect(provider.props.clientId).toBe("client-id");
});

test("leaves Auth0 session persistence to the native credentials manager", () => {
  process.env.EXPO_PUBLIC_AUTH0_DOMAIN = "tenant.auth0.com";
  process.env.EXPO_PUBLIC_AUTH0_CLIENT_ID = "client-id";
  process.env.EXPO_PUBLIC_AUTH0_AUDIENCE = "https://api.test";
  jest.replaceProperty(Platform, "OS", "ios");

  const provider = AuthProvider({ children: null }) as ReactElement;

  expect(provider.props).not.toHaveProperty("cacheLocation");
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

test.each(["web", "ios"] as const)(
  "gets the API audience access token through the supported %s credential method",
  async (platform) => {
    process.env.EXPO_PUBLIC_AUTH0_DOMAIN = "tenant.auth0.com";
    process.env.EXPO_PUBLIC_AUTH0_CLIENT_ID = "client-id";
    process.env.EXPO_PUBLIC_AUTH0_AUDIENCE = "https://api.test";
    jest.replaceProperty(Platform, "OS", platform);
    const getCredentials = jest.fn();
    const getApiCredentials = jest.fn().mockResolvedValue({
      accessToken: "api-access-token",
      tokenType: "Bearer",
      expiresAt: 1_800_000_000,
      scope: "recipes:read",
    });
    useAuth0.mockReturnValue({
      user: { sub: "auth0|recipe-owner" },
      isLoading: false,
      error: null,
      authorize: mockAuthorize,
      clearSession: jest.fn(),
      getCredentials,
      getApiCredentials,
    });

    readAccessToken = undefined;
    await render(<AuthProvider><TokenProbe /></AuthProvider>);
    if (!readAccessToken) throw new Error("AuthProvider did not expose getAccessToken");

    await expect(readAccessToken()).resolves.toBe("api-access-token");
    expect(getApiCredentials).toHaveBeenCalledWith(
      "https://api.test",
      "openid profile email offline_access recipes:read recipes:write ratings:write",
    );
    expect(getCredentials).not.toHaveBeenCalled();
  },
);
