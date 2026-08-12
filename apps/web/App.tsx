import { StatusBar } from "expo-status-bar";
import { useCallback, useMemo, useState } from "react";
import { Text, View } from "react-native";

import { getApiBases } from "./src/api/bases";
import { createApiClient } from "./src/api/client";
import { createCatalogApi } from "./src/api/catalog";
import { createIngestionApi } from "./src/api/ingestion";
import { AuthProvider, useAuth } from "./src/auth/AuthProvider";
import { getAuth0Config } from "./src/auth/config";
import { CreateRecipeScreen } from "./src/screens/CreateRecipeScreen";
import { ImportScreen } from "./src/screens/ImportScreen";
import { ImportSessionProvider } from "./src/imports/ImportSessionProvider";
import { LandingScreen } from "./src/screens/LandingScreen";
import { RecipeDetailScreen } from "./src/screens/RecipeDetailScreen";
import { RecipeListScreen } from "./src/screens/RecipeListScreen";
import { sharedStyles } from "./src/theme";

export type Route =
  | { name: "landing" }
  | { name: "list" }
  | { name: "detail"; recipeId: string }
  | { name: "create" }
  | { name: "import" };

function readAuthConfigured(): boolean {
  try {
    getAuth0Config();
    return true;
  } catch {
    return false;
  }
}

function AuthenticatedApp() {
  const auth = useAuth();
  const [route, setRoute] = useState<Route>({ name: "landing" });
  const [loginError, setLoginError] = useState<string | null>(null);

  const apiBases = useMemo(() => getApiBases(), []);
  const client = useMemo(
    () => createApiClient(auth.getAccessToken, apiBases),
    [apiBases, auth.getAccessToken],
  );
  const catalog = useMemo(() => createCatalogApi(client), [client]);
  const ingestion = useMemo(() => createIngestionApi(client), [client]);

  const goLanding = useCallback(() => {
    setRoute({ name: "landing" });
  }, []);

  const handleUnauthorized = useCallback(() => {
    setLoginError("Session expired. Please log in again.");
    setRoute({ name: "landing" });
    void auth.logout().catch(() => {
      // Session clear failed; landing still shows Log in once isAuthenticated updates,
      // or the error message prompts the user to try again.
    });
  }, [auth]);

  const handleLogin = useCallback(async () => {
    setLoginError(null);
    try {
      // Web: login redirects away; the promise intentionally never resolves.
      // Show status so a failed/blocked redirect is visible.
      setLoginError("Redirecting to Auth0…");
      await auth.login();
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : "Login failed");
    }
  }, [auth]);

  const handleLogout = useCallback(async () => {
    setLoginError(null);
    try {
      await auth.logout();
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : "Logout failed");
    } finally {
      setRoute({ name: "landing" });
    }
  }, [auth]);

  let content;
  switch (route.name) {
    case "landing":
      content = (
        <LandingScreen
          authConfigured
          isLoading={auth.isLoading}
          isAuthenticated={auth.isAuthenticated}
          errorMessage={loginError ?? auth.errorMessage}
          onLogin={() => void handleLogin()}
          onContinue={() => setRoute({ name: "list" })}
        />
      );
      break;
    case "list":
      content = (
        <RecipeListScreen
          catalog={catalog}
          onOpenDetail={(recipeId) => setRoute({ name: "detail", recipeId })}
          onCreate={() => setRoute({ name: "create" })}
          onImport={() => setRoute({ name: "import" })}
          onLogout={() => void handleLogout()}
          onUnauthorized={handleUnauthorized}
        />
      );
      break;
    case "detail":
      content = (
        <RecipeDetailScreen
          recipeId={route.recipeId}
          catalog={catalog}
          onBack={() => setRoute({ name: "list" })}
          onUnauthorized={handleUnauthorized}
        />
      );
      break;
    case "create":
      content = (
        <CreateRecipeScreen
          catalog={catalog}
          onCreated={(recipeId) => setRoute({ name: "detail", recipeId })}
          onBack={() => setRoute({ name: "list" })}
          onUnauthorized={handleUnauthorized}
        />
      );
      break;
    case "import":
      content = (
        <ImportSessionProvider ingestion={ingestion} onUnauthorized={handleUnauthorized}>
          <ImportScreen onBack={() => setRoute({ name: "list" })} />
        </ImportSessionProvider>
      );
      break;
    default: {
      const _exhaustive: never = route;
      void _exhaustive;
      content = (
        <View style={sharedStyles.centered}>
          <Text style={sharedStyles.error}>Unknown route</Text>
          <LandingScreen
            authConfigured
            isLoading={false}
            isAuthenticated={false}
            onLogin={goLanding}
            onContinue={goLanding}
          />
        </View>
      );
    }
  }

  return (
    <>
      {content}
      <StatusBar style="light" />
    </>
  );
}

export default function App() {
  const authConfigured = readAuthConfigured();

  if (!authConfigured) {
    return (
      <>
        <LandingScreen
          authConfigured={false}
          isLoading={false}
          isAuthenticated={false}
          onLogin={() => undefined}
          onContinue={() => undefined}
        />
        <StatusBar style="light" />
      </>
    );
  }

  return (
    <AuthProvider>
      <AuthenticatedApp />
    </AuthProvider>
  );
}
