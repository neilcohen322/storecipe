import { render } from "@testing-library/react-native";
import { Text } from "react-native";

import { AppProviders } from "../AppProviders";
import { useApi } from "../../api/ApiProvider";
import { useAuth } from "../../auth/AuthProvider";

const mockAuthorize = jest.fn();
const mockClearSession = jest.fn();
const mockGetCredentials = jest.fn();
const originalWindow = globalThis.window;

jest.mock("react-native-auth0", () => ({
  Auth0Provider: ({ children }: { children: React.ReactNode }) => children,
  useAuth0: () => ({
    user: { sub: "auth0|recipe-owner" },
    isLoading: false,
    error: null,
    authorize: mockAuthorize,
    clearSession: mockClearSession,
    getCredentials: mockGetCredentials,
  }),
}));

jest.mock("react-native-safe-area-context", () => ({
  SafeAreaProvider: ({ children }: { children: React.ReactNode }) => children,
}));

function ProviderProbe() {
  const auth = useAuth();
  const { client } = useApi();

  return (
    <Text>
      {auth.isAuthenticated ? "authenticated" : "anonymous"}:{typeof client.getJson}
    </Text>
  );
}

beforeEach(() => {
  delete process.env.EXPO_PUBLIC_E2E_MODE;
  process.env.EXPO_PUBLIC_AUTH0_DOMAIN = "tenant.auth0.com";
  process.env.EXPO_PUBLIC_AUTH0_CLIENT_ID = "client-id";
  process.env.EXPO_PUBLIC_AUTH0_AUDIENCE = "https://api.test";
  mockGetCredentials.mockResolvedValue({ accessToken: "test-api-token" });
});

afterEach(() => {
  Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
});

it("uses fixture authentication only when the build-time E2E flag is true", async () => {
  process.env.EXPO_PUBLIC_E2E_MODE = "true";
  delete process.env.EXPO_PUBLIC_AUTH0_DOMAIN;

  const { getByText } = await render(
    <AppProviders>
      <ProviderProbe />
    </AppProviders>,
  );

  expect(getByText("anonymous:function")).toBeOnTheScreen();
});

it("cannot activate fixture authentication from URL or storage in a normal build", async () => {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      location: { href: "https://storecipe.test/?e2e=true", search: "?e2e=true" },
      localStorage: { getItem: jest.fn(() => "true") },
    },
  });
  delete process.env.EXPO_PUBLIC_AUTH0_DOMAIN;

  const { getByText, queryByText } = await render(
    <AppProviders>
      <ProviderProbe />
    </AppProviders>,
  );

  expect(getByText(/Set EXPO_PUBLIC_AUTH0_DOMAIN/)).toBeOnTheScreen();
  expect(queryByText("anonymous:function")).toBeNull();
});

it("exposes Auth0 state and the authenticated API client to provider descendants", async () => {
  const { getByText } = await render(
    <AppProviders>
      <ProviderProbe />
    </AppProviders>,
  );

  expect(getByText("authenticated:function")).toBeOnTheScreen();
});

it("shows Auth0 configuration guidance without mounting AuthProvider", async () => {
  delete process.env.EXPO_PUBLIC_AUTH0_DOMAIN;

  const { getByText, queryByText } = await render(
    <AppProviders>
      <ProviderProbe />
    </AppProviders>,
  );

  expect(
    getByText(
      "Set EXPO_PUBLIC_AUTH0_DOMAIN, EXPO_PUBLIC_AUTH0_CLIENT_ID, and EXPO_PUBLIC_AUTH0_AUDIENCE to enable login.",
    ),
  ).toBeOnTheScreen();
  expect(queryByText("authenticated:function")).toBeNull();
});

it("requests login for credential errors but rethrows transient token errors", async () => {
  mockGetCredentials.mockRejectedValueOnce(
    Object.assign(new Error("Sign in"), { type: "NO_CREDENTIALS" }),
  );

  const { client } = await renderWithProviders();

  await expect(client.getJson("/v1/recipes")).rejects.toThrow("Sign in");
  expect(mockAuthorize).toHaveBeenCalledTimes(1);

  mockAuthorize.mockClear();
  mockGetCredentials.mockRejectedValueOnce(new Error("NO_NETWORK"));

  await expect(client.getJson("/v1/recipes")).rejects.toThrow("NO_NETWORK");
  expect(mockAuthorize).not.toHaveBeenCalled();
});

async function renderWithProviders() {
  let client: ReturnType<typeof useApi>["client"] | undefined;

  function ClientProbe() {
    client = useApi().client;
    return null;
  }

  await render(
    <AppProviders>
      <ClientProbe />
    </AppProviders>,
  );

  if (!client) {
    throw new Error("ApiProvider did not expose a client");
  }

  return { client };
}
