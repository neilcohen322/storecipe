import { useCallback, useMemo } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useWindowDimensions } from "react-native";
import { authPresentation } from "@storecipe/auth-provider";

import { createCatalogApi } from "../api/catalog";
import { createIngestionApi } from "../api/ingestion";
import { useApi } from "../api/ApiProvider";
import { useAuth } from "../auth/AuthProvider";
import { AuthGate } from "../auth/AuthGate";
import { CreateRecipeScreen } from "../screens/CreateRecipeScreen";
import { getLayoutMode } from "../components/AppShell";
import { ImportScreen } from "../screens/ImportScreen";
import { ImportHistoryScreen } from "../screens/ImportHistoryScreen";
import { LandingScreen } from "../screens/LandingScreen";
import { RecipeDetailScreen } from "../screens/RecipeDetailScreen";
import { RecipeListScreen } from "../screens/RecipeListScreen";

function useLegacyApis() {
  const { client } = useApi();
  return {
    catalog: useMemo(() => createCatalogApi(client), [client]),
    ingestion: useMemo(() => createIngestionApi(client), [client]),
  };
}

function useUnauthorizedHandler() {
  const router = useRouter();
  return useCallback(() => {
    router.replace("/");
  }, [router]);
}

export function LandingRouteAdapter() {
  const auth = useAuth();
  const router = useRouter();
  return (
    <LandingScreen
      authPresentation={authPresentation}
      authConfigured
      isLoading={auth.isLoading}
      isAuthenticated={auth.isAuthenticated}
      errorMessage={auth.errorMessage}
      onLogin={() => void auth.login()}
      onContinue={() => router.replace("/recipes")}
    />
  );
}

export function RecipesRouteAdapter() {
  const { catalog } = useLegacyApis();
  const auth = useAuth();
  const router = useRouter();
  const onUnauthorized = useUnauthorizedHandler();
  const { width } = useWindowDimensions();
  return (
    <AuthGate>
      <RecipeListScreen
        catalog={catalog}
        onOpenDetail={(recipeId) =>
          router.push({ pathname: "/recipes/[recipeId]", params: { recipeId } })
        }
        onCreate={() => router.push("/recipes/new")}
        onImport={() => router.push("/imports")}
        onLogout={() => void auth.logout().then(() => router.replace("/"))}
        onUnauthorized={onUnauthorized}
        layoutMode={getLayoutMode(width)}
      />
    </AuthGate>
  );
}

export function NewRecipeRouteAdapter() {
  const { catalog, ingestion } = useLegacyApis();
  const router = useRouter();
  const onUnauthorized = useUnauthorizedHandler();
  const { width } = useWindowDimensions();
  return (
    <AuthGate>
      <CreateRecipeScreen
        catalog={catalog}
        ingestion={ingestion}
        onCreated={(recipeId) =>
          router.replace({ pathname: "/recipes/[recipeId]", params: { recipeId } })
        }
        onBack={() => router.back()}
        onUnauthorized={onUnauthorized}
        layoutMode={getLayoutMode(width)}
      />
    </AuthGate>
  );
}

export function RecipeDetailRouteAdapter() {
  const { catalog } = useLegacyApis();
  const router = useRouter();
  const onUnauthorized = useUnauthorizedHandler();
  const { recipeId } = useLocalSearchParams<{ recipeId?: string | string[] }>();
  return (
    <AuthGate>
      <RecipeDetailScreen
        recipeId={recipeId}
        catalog={catalog}
        onBack={() => router.back()}
        onUnauthorized={onUnauthorized}
      />
    </AuthGate>
  );
}

export function ImportsRouteAdapter() {
  const router = useRouter();
  return (
    <AuthGate>
      <ImportHistoryScreen onNewImport={() => router.push("/imports/new")} />
    </AuthGate>
  );
}

export function NewImportRouteAdapter() {
  const router = useRouter();
  return (
    <AuthGate>
      <ImportScreen onBack={() => router.back()} />
    </AuthGate>
  );
}
