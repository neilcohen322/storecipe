import type { ReactElement } from "react";
import { fireEvent, render } from "@testing-library/react-native";
import { Pressable, Text } from "react-native";

import { AuthProvider, useAuth } from "../AuthProvider";

jest.mock("react-native-auth0", () => ({
  Auth0Provider: ({ children }: { children: React.ReactNode }) => children,
  useAuth0: jest.fn(),
}));

const { useAuth0 } = jest.requireMock("react-native-auth0") as { useAuth0: jest.Mock };
const mockAuthorize = jest.fn();

function LoginButton() {
  const { login } = useAuth();
  return <Pressable accessibilityRole="button" accessibilityLabel="Continue with Google" onPress={() => void login()}><Text>Continue with Google</Text></Pressable>;
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

test("starts login through the Google Auth0 connection", async () => {
  process.env.EXPO_PUBLIC_AUTH0_DOMAIN = "tenant.auth0.com";
  process.env.EXPO_PUBLIC_AUTH0_CLIENT_ID = "client-id";
  process.env.EXPO_PUBLIC_AUTH0_AUDIENCE = "https://api.test";
  mockAuthorize.mockReset().mockResolvedValue(undefined);
  useAuth0.mockReturnValue({ user: null, isLoading: false, error: null, authorize: mockAuthorize, clearSession: jest.fn(), getCredentials: jest.fn() });

  const screen = await render(<AuthProvider><LoginButton /></AuthProvider>);
  fireEvent.press(screen.getByRole("button", { name: "Continue with Google" }));

  expect(mockAuthorize).toHaveBeenCalledWith(expect.objectContaining({ connection: "google-oauth2", audience: "https://api.test" }));
});
