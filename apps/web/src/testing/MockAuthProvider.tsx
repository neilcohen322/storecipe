import { type PropsWithChildren, useCallback, useMemo, useState } from "react";
import { Platform } from "react-native";

import { AuthContext, type AuthContextValue } from "../auth/AuthProvider";

const fixtureUser = {
  name: "E2E Recipe Owner",
  email: "recipe.owner@example.test",
};

export const E2E_AUTH_STORAGE_KEY = "storecipe.e2e-authenticated";

function readFixtureSession(): boolean {
  if (Platform.OS !== "web" || typeof window === "undefined") return false;
  try { return window.localStorage.getItem(E2E_AUTH_STORAGE_KEY) === "true"; } catch { return false; }
}

function writeFixtureSession(authenticated: boolean): void {
  if (Platform.OS !== "web" || typeof window === "undefined") return;
  try { window.localStorage.setItem(E2E_AUTH_STORAGE_KEY, String(authenticated)); } catch { /* fixture persistence is optional */ }
}

export function MockAuthProvider({ children }: PropsWithChildren) {
  const [isAuthenticated, setIsAuthenticated] = useState(readFixtureSession);
  const login = useCallback(async () => { writeFixtureSession(true); setIsAuthenticated(true); }, []);
  const logout = useCallback(async () => { writeFixtureSession(false); setIsAuthenticated(false); }, []);
  const getAccessToken = useCallback(async () => "e2e-intercepted-api-token", []);
  const value = useMemo<AuthContextValue>(() => ({
    isLoading: false,
    isAuthenticated,
    errorMessage: null,
    user: isAuthenticated ? fixtureUser : null,
    login,
    logout,
    getAccessToken,
  }), [getAccessToken, isAuthenticated, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
