import { useRouter } from "expo-router";

import { useAuth } from "../src/auth/AuthProvider";
import { Button, EmptyState, Screen } from "../src/components";

export default function NotFoundRoute() {
  const auth = useAuth();
  const router = useRouter();
  const destination = auth.isAuthenticated ? "/recipes" : "/";
  const label = auth.isAuthenticated ? "Return to recipes" : "Return to sign in";
  return (
    <Screen>
      <EmptyState
        title="Page not found"
        description="This page does not exist or is no longer available."
        action={<Button label={label} onPress={() => router.replace(destination)} />}
      />
    </Screen>
  );
}
