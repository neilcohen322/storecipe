import { Redirect } from "expo-router";

import { useAuth } from "../src/auth/AuthProvider";
import { returnPathStorage } from "../src/auth/returnPathStorage";
import { LandingScreen } from "../src/screens/LandingScreen";

export default function IndexRoute() {
  const auth = useAuth();
  if (auth.isAuthenticated) return <Redirect href={returnPathStorage.consume() ?? "/recipes"} />;
  return <LandingScreen authConfigured isLoading={auth.isLoading} isAuthenticated={false} errorMessage={auth.errorMessage} onLogin={() => void auth.login()} onContinue={() => undefined} />;
}
