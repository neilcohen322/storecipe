import {
  createContext,
  type ComponentProps,
  type PropsWithChildren,
  useCallback,
  useContext,
  useMemo,
} from "react";
import { Platform } from "react-native";
import {
  Auth0Provider as ReactNativeAuth0Provider,
  useAuth0,
} from "react-native-auth0";

import { getAuth0Config } from "./config";

const AUTH_SCOPE =
  "openid profile email offline_access recipes:read recipes:write ratings:write";

export type AuthContextValue = {
  isLoading: boolean;
  isAuthenticated: boolean;
  errorMessage: string | null;
  user: { name?: string; email?: string; picture?: string } | null;
  login(): Promise<void>;
  logout(): Promise<void>;
  getAccessToken(): Promise<string>;
};

export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function webRedirectUrl(): string | undefined {
  if (Platform.OS !== "web") {
    return undefined;
  }
  if (typeof window === "undefined" || !window.location?.origin) {
    return undefined;
  }
  return window.location.origin;
}

function AuthSession({
  audience,
  children,
}: PropsWithChildren<{ audience: string }>) {
  const { user, isLoading, error, authorize, clearSession, getCredentials } =
    useAuth0();

  const login = useCallback(async () => {
    // On web, authorize() calls loginWithRedirect. Passing an explicit
    // redirectUrl avoids redirect_uri: undefined wiping the SPA default.
    const redirectUrl = webRedirectUrl();
    await authorize({
      audience,
      connection: "google-oauth2",
      scope: AUTH_SCOPE,
      ...(redirectUrl ? { redirectUrl } : {}),
    });
  }, [audience, authorize]);

  const logout = useCallback(async () => {
    const returnToUrl = webRedirectUrl();
    await clearSession(returnToUrl ? { returnToUrl } : undefined);
  }, [clearSession]);

  const getAccessToken = useCallback(async () => {
    const credentials = await getCredentials(AUTH_SCOPE, 0, { audience });
    return credentials.accessToken;
  }, [audience, getCredentials]);

  const value = useMemo<AuthContextValue>(
    () => ({
      isLoading,
      isAuthenticated: user !== null,
      errorMessage: error?.message ?? null,
      user: user ? { name: user.name, email: user.email, picture: user.picture } : null,
      login,
      logout,
      getAccessToken,
    }),
    [error?.message, getAccessToken, isLoading, login, logout, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function AuthProvider({ children }: PropsWithChildren) {
  const { domain, clientId, audience } = getAuth0Config();
  // Auth0Provider's public props type is platform-agnostic Auth0Options; these
  // web Auth0 SPA options are consumed at runtime by the web adapter.
  const providerProps = {
    domain,
    clientId,
    useDPoP: false,
    useRefreshTokens: true,
    ...(Platform.OS === "web"
      ? { cacheLocation: "localstorage" as const }
      : {}),
  } as ComponentProps<typeof ReactNativeAuth0Provider>;

  return (
    <ReactNativeAuth0Provider {...providerProps}>
      <AuthSession audience={audience}>{children}</AuthSession>
    </ReactNativeAuth0Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
