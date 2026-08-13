import { Redirect } from "expo-router";
import { authPresentation } from "@storecipe/auth-provider";

import { useAuth } from "../src/auth/AuthProvider";
import { returnPathStorage, useCommittedReturnPath } from "../src/auth/returnPathStorage";
import { LandingScreen } from "../src/screens/LandingScreen";

export default function IndexRoute() {
  const auth = useAuth();
  const destination = useCommittedReturnPath(returnPathStorage, auth.isAuthenticated, "/recipes");
  if (auth.isAuthenticated) return <Redirect href={destination} />;
  return <LandingScreen authPresentation={authPresentation} authConfigured isLoading={auth.isLoading} isAuthenticated={false} errorMessage={auth.errorMessage} onLogin={() => void auth.login()} onContinue={() => undefined} />;
}
