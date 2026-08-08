import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useMemo,
} from "react";
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
  login(): Promise<void>;
  logout(): Promise<void>;
  getAccessToken(): Promise<string>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function AuthSession({
  audience,
  children,
}: PropsWithChildren<{ audience: string }>) {
  const { user, isLoading, authorize, clearSession, getCredentials } = useAuth0();

  const login = useCallback(async () => {
    await authorize({ audience, scope: AUTH_SCOPE });
  }, [audience, authorize]);

  const logout = useCallback(async () => {
    await clearSession();
  }, [clearSession]);

  const getAccessToken = useCallback(async () => {
    const credentials = await getCredentials(AUTH_SCOPE, 0, { audience });
    return credentials.accessToken;
  }, [audience, getCredentials]);

  const value = useMemo<AuthContextValue>(
    () => ({
      isLoading,
      isAuthenticated: user !== null,
      login,
      logout,
      getAccessToken,
    }),
    [getAccessToken, isLoading, login, logout, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function AuthProvider({ children }: PropsWithChildren) {
  const { domain, clientId, audience } = getAuth0Config();

  return (
    <ReactNativeAuth0Provider domain={domain} clientId={clientId}>
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
