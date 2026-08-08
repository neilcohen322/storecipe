import type { ReactElement } from "react";

import { AuthProvider } from "../AuthProvider";

jest.mock("react-native-auth0", () => ({
  Auth0Provider: () => null,
  useAuth0: jest.fn(),
}));

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
