import { useMemo } from "react";
import { Link, useLocalSearchParams, useRouter } from "expo-router";
import { Text, View } from "react-native";

import { createCatalogApi } from "../api/catalog";
import { createIngestionApi } from "../api/ingestion";
import { useApi } from "../api/ApiProvider";
import { useAuth } from "../auth/AuthProvider";
import { AuthGate } from "../auth/AuthGate";
import { CreateRecipeScreen } from "../screens/CreateRecipeScreen";
import { ImportScreen } from "../screens/ImportScreen";
import { LandingScreen } from "../screens/LandingScreen";
import { RecipeDetailScreen } from "../screens/RecipeDetailScreen";
import { RecipeListScreen } from "../screens/RecipeListScreen";
import { sharedStyles } from "../theme";

function useLegacyApis() {
  const { client } = useApi();
  return {
    catalog: useMemo(() => createCatalogApi(client), [client]),
    ingestion: useMemo(() => createIngestionApi(client), [client]),
  };
}

function useUnauthorizedHandler() {
  const auth = useAuth();
  const router = useRouter();
  return () => {
    void auth.logout().catch(() => undefined);
    router.replace("/");
  };
}

export function LandingRouteAdapter() {
  const auth = useAuth();
  const router = useRouter();
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

export function RecipesRouteAdapter() {
  const { catalog } = useLegacyApis();
  const auth = useAuth();
  const router = useRouter();
  const onUnauthorized = useUnauthorizedHandler();
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
      />
    </AuthGate>
  );
}

export function NewRecipeRouteAdapter() {
  const { catalog } = useLegacyApis();
  const router = useRouter();
  const onUnauthorized = useUnauthorizedHandler();
  return (
    <AuthGate>
      <CreateRecipeScreen
        catalog={catalog}
        onCreated={(recipeId) =>
          router.replace({ pathname: "/recipes/[recipeId]", params: { recipeId } })
        }
        onBack={() => router.back()}
        onUnauthorized={onUnauthorized}
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
  const { ingestion } = useLegacyApis();
  const router = useRouter();
  const onUnauthorized = useUnauthorizedHandler();
  return (
    <AuthGate>
      <ImportScreen ingestion={ingestion} onBack={() => router.back()} onUnauthorized={onUnauthorized} />
    </AuthGate>
  );
}

export function TemporaryRoutePlaceholder({ title }: { title: "Account" | "More" }) {
  return (
    <View style={sharedStyles.centered}>
      <Text style={sharedStyles.heading}>{title}</Text>
      <Text style={sharedStyles.body}>This area is being prepared for the responsive shell.</Text>
      <Link href="/recipes" style={sharedStyles.buttonText}>
        Back to recipes
      </Link>
    </View>
  );
}
