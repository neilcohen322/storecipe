import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  Text,
  View,
} from "react-native";

import { ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, RecipeQueryItem } from "../api/catalog";
import { colors, sharedStyles } from "../theme";

export type RecipeListScreenProps = {
  catalog: ReturnType<typeof createCatalogApi>;
  onOpenDetail(recipeId: string): void;
  onCreate(): void;
  onImport(): void;
  onLogout(): void;
  onUnauthorized(): void;
};

export function RecipeListScreen({
  catalog,
  onOpenDetail,
  onCreate,
  onImport,
  onLogout,
  onUnauthorized,
}: RecipeListScreenProps) {
  const [items, setItems] = useState<RecipeQueryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await catalog.listRecipes({ sort: ["updatedAt:desc"], limit: 50 });
      setItems(page.items);
    } catch (err) {
      if (err instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load recipes");
    } finally {
      setLoading(false);
    }
  }, [catalog, onUnauthorized]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <View style={sharedStyles.screen}>
      <Text style={sharedStyles.heading}>Recipes</Text>

      <View style={sharedStyles.buttonRow}>
        <Pressable accessibilityRole="button" onPress={onCreate} style={sharedStyles.button}>
          <Text style={sharedStyles.buttonText}>Create</Text>
        </Pressable>
        <Pressable accessibilityRole="button" onPress={onImport} style={sharedStyles.button}>
          <Text style={sharedStyles.buttonText}>Import</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          onPress={() => void load()}
          style={sharedStyles.buttonSecondary}
        >
          <Text style={sharedStyles.buttonText}>Refresh</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          onPress={onLogout}
          style={sharedStyles.buttonSecondary}
        >
          <Text style={sharedStyles.buttonText}>Log out</Text>
        </Pressable>
      </View>

      {loading ? (
        <ActivityIndicator color={colors.badge} style={{ marginTop: 24 }} />
      ) : error ? (
        <Text style={sharedStyles.error}>{error}</Text>
      ) : items.length === 0 ? (
        <Text style={sharedStyles.note}>No recipes yet. Create one or import from a URL.</Text>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.recipe.id}
          renderItem={({ item }) => (
            <Pressable
              accessibilityRole="button"
              onPress={() => onOpenDetail(item.recipe.id)}
              style={sharedStyles.row}
            >
              <Text style={sharedStyles.rowTitle}>{item.recipe.title}</Text>
              <Text style={sharedStyles.note}>
                {item.recipe.rating != null
                  ? `Rating ${item.recipe.rating}/5`
                  : "Unrated"}
              </Text>
            </Pressable>
          )}
        />
      )}
    </View>
  );
}
