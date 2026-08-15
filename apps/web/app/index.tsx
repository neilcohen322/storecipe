import { Redirect, useRouter, type Href } from "expo-router";
import { authPresentation } from "@storecipe/auth-provider";

import { useAuth } from "../src/auth/AuthProvider";
import { returnPathStorage, useCommittedReturnPath } from "../src/auth/returnPathStorage";
import { LandingScreen } from "../src/screens/LandingScreen";

export default function IndexRoute() {
  const auth = useAuth();
  const router = useRouter();
  const savedReturnPath = auth.isAuthenticated ? returnPathStorage.peek() : null;
  const destination = useCommittedReturnPath(
    returnPathStorage,
    Boolean(auth.isAuthenticated && savedReturnPath),
    savedReturnPath ?? "/recipes",
  );
  if (auth.isAuthenticated && savedReturnPath) {
    return <Redirect href={destination as Href} />;
  }
  return (
    <LandingScreen
      authPresentation={authPresentation}
      authConfigured
      isLoading={auth.isLoading}
      isAuthenticated={auth.isAuthenticated}
      errorMessage={auth.errorMessage}
      onLogin={() => {
        returnPathStorage.save("/recipes");
        void auth.login();
      }}
      onContinue={() => router.replace("/recipes")}
    />
  );
}
