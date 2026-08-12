import { useCallback, useEffect, useRef, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import { ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, Recipe } from "../api/catalog";
import { Button, ErrorState, InlineNotice, LoadingState, OfflineBanner, PageHeader, RatingControl, RecipeMedia, Screen, Section } from "../components";

type DetailError = "none" | "notFound" | "offline" | "generic";

export type RecipeDetailScreenProps = {
  recipeId: unknown;
  catalog: ReturnType<typeof createCatalogApi>;
  onBack(): void;
  onUnauthorized(): void;
};

function routeRecipeId(value: unknown): string | null {
  return typeof value === "string" && value.trim() && !/\s/.test(value) ? value.trim() : null;
}

function isOfflineError(error: unknown): boolean {
  return error instanceof ApiNetworkError || (typeof error === "object" && error !== null && ((error as { code?: unknown }).code === "ERR_NETWORK" || (error as { code?: unknown }).code === "NETWORK_ERROR"));
}

function isNotFoundError(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { status?: unknown }).status === 404;
}

export function RecipeDetailScreen({ recipeId, catalog, onBack, onUnauthorized }: RecipeDetailScreenProps) {
  const id = routeRecipeId(recipeId);
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [loading, setLoading] = useState(Boolean(id));
  const [error, setError] = useState<DetailError>(id ? "none" : "notFound");
  const [savingRating, setSavingRating] = useState(false);
  const [ratingRetry, setRatingRetry] = useState<number | null>(null);
  const mounted = useRef(true);
  const loadRequestId = useRef(0);
  const ratingRequestId = useRef(0);

  const load = useCallback(async () => {
    if (!id) {
      setRecipe(null); setLoading(false); setError("notFound");
      return;
    }
    const requestId = ++loadRequestId.current;
    setLoading(true); setError("none"); setRecipe(null); setRatingRetry(null);
    try {
      const next = await catalog.getRecipe(id);
      if (!mounted.current || requestId !== loadRequestId.current) return;
      setRecipe(next);
    } catch (caught) {
      if (!mounted.current || requestId !== loadRequestId.current) return;
      if (caught instanceof ApiUnauthorizedError) { onUnauthorized(); return; }
      setError(isNotFoundError(caught) ? "notFound" : isOfflineError(caught) ? "offline" : "generic");
    } finally {
      if (mounted.current && requestId === loadRequestId.current) setLoading(false);
    }
  }, [catalog, id, onUnauthorized]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; loadRequestId.current += 1; ratingRequestId.current += 1; };
  }, [load]);

  const setRating = async (value: number) => {
    if (!id || !recipe || savingRating || value < 1 || value > 5) return;
    const requestId = ++ratingRequestId.current;
    const priorRating = recipe.rating;
    setSavingRating(true); setRatingRetry(null);
    setRecipe((current) => current?.id === id ? { ...current, rating: value } : current);
    try {
      const rating = await catalog.putRating(id, value as 1 | 2 | 3 | 4 | 5);
      if (!mounted.current || requestId !== ratingRequestId.current) return;
      setRecipe((current) => current?.id === id ? { ...current, rating: rating.value } : current);
    } catch (caught) {
      if (!mounted.current || requestId !== ratingRequestId.current) return;
      if (caught instanceof ApiUnauthorizedError) { onUnauthorized(); return; }
      setRecipe((current) => current?.id === id ? { ...current, rating: priorRating } : current);
      setRatingRetry(value);
    } finally {
      if (mounted.current && requestId === ratingRequestId.current) setSavingRating(false);
    }
  };

  const errorContent = error === "notFound"
    ? <ErrorState title="We couldn't find that recipe." action={<Button label="Try again" onPress={() => void load()} />} />
    : error === "offline"
      ? <><OfflineBanner message="You’re offline. Check your connection and try again." /><Button label="Try again" onPress={() => void load()} /></>
      : <ErrorState title="We couldn't load this recipe. Please try again." action={<Button label="Try again" onPress={() => void load()} />} />;

  return <Screen><Button label="Back to list" variant="secondary" onPress={onBack} />
    {loading ? <LoadingState label="Loading recipe" /> : error !== "none" && !recipe ? errorContent : recipe ? <View style={styles.detail}>
      <View testID="recipe-detail-media" style={styles.mediaSlot}><RecipeMedia title={recipe.title} tags={recipe.tags} /></View>
      <PageHeader title={recipe.title} subtitle={[recipe.servings ? `Serves ${recipe.servings}` : null, recipe.totalMinutes ? `${recipe.totalMinutes} min` : null].filter(Boolean).join(" · ") || undefined} />
      <Section title="Rating"><Text>{recipe.rating ? `${recipe.rating} out of 5` : "Not rated"}</Text><RatingControl value={recipe.rating ?? 0} onChange={(value) => void setRating(value)} disabled={savingRating} />{ratingRetry ? <View style={styles.ratingError}><InlineNotice tone="error" message="We couldn't save your rating." /><Button label="Try rating again" variant="secondary" onPress={() => void setRating(ratingRetry)} /></View> : null}</Section>
      <View testID="recipe-detail-columns" style={styles.columns}>
        <Section title="Ingredients" accessibilityRole="list" accessibilityLabel="Ingredients" style={styles.ingredients}>{recipe.ingredients.length ? recipe.ingredients.map((ingredient, index) => <View key={`${ingredient.rawText}-${index}`} accessibilityRole={"listitem" as never} accessibilityLabel={ingredient.rawText}><Text style={styles.listItem}>• {ingredient.rawText}</Text></View>) : <Text>None listed.</Text>}</Section>
        <Section title="Instructions" accessibilityRole="list" accessibilityLabel="Instructions" style={styles.instructions}>{recipe.instructions.length ? recipe.instructions.map((step, index) => <View key={`${index}-${step.slice(0, 24)}`} accessibilityRole={"listitem" as never}><Text style={styles.step}>{index + 1}. {step}</Text></View>) : <Text>None listed.</Text>}</Section>
      </View>
    </View> : null}
  </Screen>;
}

const styles = StyleSheet.create({ detail: { gap: 16 }, mediaSlot: { minHeight: 280, width: "100%" }, columns: { flexDirection: "row", flexWrap: "wrap", gap: 24 }, ingredients: { flexGrow: 1, flexBasis: 280 }, instructions: { flexGrow: 2, flexBasis: 520 }, listItem: { marginBottom: 8 }, step: { marginBottom: 12, lineHeight: 24 }, ratingError: { gap: 8 } });
