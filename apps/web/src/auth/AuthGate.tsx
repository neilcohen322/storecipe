import type { PropsWithChildren } from "react";
import { useRouter } from "expo-router";

import { LandingScreen } from "../screens/LandingScreen";
import { useAuth } from "./AuthProvider";

export function AuthGate({ children }: PropsWithChildren) {
  const auth = useAuth();
  const router = useRouter();

  if (auth.isLoading || !auth.isAuthenticated) {
    return (
      <LandingScreen
        authConfigured
        isLoading={auth.isLoading}
        isAuthenticated={auth.isAuthenticated}
        errorMessage={auth.errorMessage}
        onLogin={() => void auth.login()}
        onContinue={() => router.replace("/recipes")}
      />
    );
  }

  return children;
}
