import { render } from "@testing-library/react-native";
import { Text } from "react-native";

import { AppProviders } from "../AppProviders";
import { useApi } from "../../api/ApiProvider";
import { useAuth } from "../../auth/AuthProvider";

const mockAuthorize = jest.fn();
const mockClearSession = jest.fn();
const mockGetCredentials = jest.fn();
const mockGetApiCredentials = jest.fn();
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
    getApiCredentials: mockGetApiCredentials,
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
  mockAuthorize.mockReset().mockResolvedValue(undefined);
  mockClearSession.mockReset().mockResolvedValue(undefined);
  mockGetCredentials.mockReset();
  mockGetApiCredentials.mockReset().mockResolvedValue({ accessToken: "test-api-token" });
});

afterEach(() => {
  Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
});

it("does not activate fixture authentication from a runtime E2E flag in the production seam", async () => {
  process.env.EXPO_PUBLIC_E2E_MODE = "true";
  delete process.env.EXPO_PUBLIC_AUTH0_DOMAIN;

  const { getByText } = await render(
    <AppProviders>
      <ProviderProbe />
    </AppProviders>,
  );

  expect(getByText(/Set EXPO_PUBLIC_AUTH0_DOMAIN/)).toBeOnTheScreen();
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

it("does not authorize or fetch when the authenticated first request cannot get API credentials", async () => {
  const credentialError = Object.assign(new Error("Sign in"), { type: "NO_CREDENTIALS" });
  mockGetCredentials.mockRejectedValueOnce(credentialError);
  mockGetApiCredentials.mockRejectedValueOnce(credentialError);
  const fetchMock = jest.fn();
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const { client } = await renderWithProviders();

  await expect(client.getJson("/v1/recipes")).rejects.toThrow("Sign in");
  expect(mockAuthorize).not.toHaveBeenCalled();
  expect(fetchMock).not.toHaveBeenCalled();
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
