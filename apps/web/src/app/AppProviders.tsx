import type { PropsWithChildren } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ApiProvider } from "../api/ApiProvider";
import { AuthProvider } from "../auth/AuthProvider";
import { getAuth0Config } from "../auth/config";
import { LandingScreen } from "../screens/LandingScreen";
import { MockAuthProvider } from "../testing/MockAuthProvider";
import { ThemeProvider } from "../theme/ThemeProvider";

function isE2EMode(): boolean {
  return process.env.EXPO_PUBLIC_E2E_MODE === "true";
}

function isAuth0Configured(): boolean {
  try {
    getAuth0Config();
    return true;
  } catch {
    return false;
  }
}

export function AppProviders({ children }: PropsWithChildren) {
  const Auth = isE2EMode() ? MockAuthProvider : AuthProvider;
  const authConfigured = isAuth0Configured();

  return (
    <SafeAreaProvider>
      <ThemeProvider>
        {authConfigured || isE2EMode() ? (
          <Auth>
            <ApiProvider>{children}</ApiProvider>
          </Auth>
        ) : (
          <LandingScreen
            authConfigured={false}
            isLoading={false}
            isAuthenticated={false}
            onLogin={() => undefined}
            onContinue={() => undefined}
          />
        )}
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
