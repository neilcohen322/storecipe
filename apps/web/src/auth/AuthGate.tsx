import type { PropsWithChildren } from "react";
import { Redirect, usePathname, useRouter } from "expo-router";
import { authPresentation } from "@storecipe/auth-provider";

import { LandingScreen } from "../screens/LandingScreen";
import { useAuth } from "./AuthProvider";
import { returnPathStorage, type ReturnPathStorage, useCommittedReturnPath } from "./returnPathStorage";

export function AuthGate({ children, returnPathStorage: storage = returnPathStorage }: PropsWithChildren<{ returnPathStorage?: ReturnPathStorage }>) {
  const auth = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const destination = useCommittedReturnPath(storage, auth.isAuthenticated, pathname);

  if (auth.isLoading || !auth.isAuthenticated) {
    return (
      <LandingScreen
        authPresentation={authPresentation}
        authConfigured
        isLoading={auth.isLoading}
        isAuthenticated={auth.isAuthenticated}
        errorMessage={auth.errorMessage}
        onLogin={() => {
          storage.save(pathname);
          void auth.login();
        }}
        onContinue={() => router.replace("/recipes")}
      />
    );
  }

  if (destination !== pathname) return <Redirect href={destination} />;

  return children;
}
