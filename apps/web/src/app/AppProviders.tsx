import type { PropsWithChildren } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ApiProvider } from "../api/ApiProvider";
import { getAuth0Config } from "../auth/config";
import { LandingScreen } from "../screens/LandingScreen";
import { ThemeProvider } from "../theme/ThemeProvider";
import { AuthProvider, isE2EFixtureBuild } from "@storecipe/auth-provider";

function isAuth0Configured(): boolean {
  try {
    getAuth0Config();
    return true;
  } catch {
    return false;
  }
}

export function AppProviders({ children }: PropsWithChildren) {
  const authConfigured = isAuth0Configured();

  return (
    <SafeAreaProvider>
      <ThemeProvider>
        {authConfigured || isE2EFixtureBuild ? (
          <AuthProvider>
            <ApiProvider>{children}</ApiProvider>
          </AuthProvider>
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
