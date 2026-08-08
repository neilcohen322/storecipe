import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Text,
  View,
} from "react-native";

import { ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, Recipe } from "../api/catalog";
import { colors, sharedStyles } from "../theme";

const RATING_VALUES = [1, 2, 3, 4, 5] as const;

export type RecipeDetailScreenProps = {
  recipeId: string;
  catalog: ReturnType<typeof createCatalogApi>;
  onBack(): void;
  onUnauthorized(): void;
};

export function RecipeDetailScreen({
  recipeId,
  catalog,
  onBack,
  onUnauthorized,
}: RecipeDetailScreenProps) {
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingRating, setSavingRating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await catalog.getRecipe(recipeId);
      setRecipe(next);
    } catch (err) {
      if (err instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load recipe");
    } finally {
      setLoading(false);
    }
  }, [catalog, onUnauthorized, recipeId]);

  useEffect(() => {
    void load();
  }, [load]);

  const setRating = async (value: (typeof RATING_VALUES)[number]) => {
    setSavingRating(true);
    setError(null);
    try {
      const rating = await catalog.putRating(recipeId, value);
      setRecipe((prev) => (prev ? { ...prev, rating: rating.value } : prev));
    } catch (err) {
      if (err instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to save rating");
    } finally {
      setSavingRating(false);
    }
  };

  return (
    <ScrollView style={sharedStyles.screen} contentContainerStyle={{ paddingBottom: 40 }}>
      <Pressable accessibilityRole="button" onPress={onBack} style={sharedStyles.buttonSecondary}>
        <Text style={sharedStyles.buttonText}>Back to list</Text>
      </Pressable>

      {loading ? (
        <ActivityIndicator color={colors.badge} style={{ marginTop: 24 }} />
      ) : error && !recipe ? (
        <Text style={sharedStyles.error}>{error}</Text>
      ) : recipe ? (
        <View style={{ marginTop: 16 }}>
          <Text style={sharedStyles.heading}>{recipe.title}</Text>

          <Text style={sharedStyles.label}>Rating</Text>
          <View style={sharedStyles.buttonRow}>
            {RATING_VALUES.map((value) => {
              const selected = recipe.rating === value;
              return (
                <Pressable
                  key={value}
                  accessibilityRole="button"
                  disabled={savingRating}
                  onPress={() => void setRating(value)}
                  style={selected ? sharedStyles.button : sharedStyles.buttonSecondary}
                >
                  <Text style={sharedStyles.buttonText}>{value}</Text>
                </Pressable>
              );
            })}
          </View>
          {error ? <Text style={sharedStyles.error}>{error}</Text> : null}

          <Text style={[sharedStyles.label, { marginTop: 16 }]}>Ingredients</Text>
          {recipe.ingredients.length === 0 ? (
            <Text style={sharedStyles.body}>None listed.</Text>
          ) : (
            recipe.ingredients.map((ingredient, index) => (
              <Text key={`${ingredient.rawText}-${index}`} style={sharedStyles.body}>
                • {ingredient.rawText}
              </Text>
            ))
          )}

          <Text style={[sharedStyles.label, { marginTop: 16 }]}>Instructions</Text>
          {recipe.instructions.length === 0 ? (
            <Text style={sharedStyles.body}>None listed.</Text>
          ) : (
            recipe.instructions.map((step, index) => (
              <Text key={`${index}-${step.slice(0, 24)}`} style={[sharedStyles.body, { marginBottom: 8 }]}>
                {index + 1}. {step}
              </Text>
            ))
          )}
        </View>
      ) : null}
    </ScrollView>
  );
}
