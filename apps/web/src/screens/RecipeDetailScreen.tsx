import { useCallback, useEffect, useRef, useState, type ComponentProps } from "react";
import { Platform, StyleSheet, Text, View } from "react-native";

import { ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, Recipe } from "../api/catalog";
import { Button, ConfirmDialog, ErrorState, InlineNotice, LoadingState, OfflineBanner, PageHeader, RatingControl, RecipeMedia, Screen, Section } from "../components";
import type { CoverImageLoader } from "../components/AuthenticatedRecipeImage";
import { blobFromPickerUri, coverImageErrorMessage, pickRecipeCoverImage, pickerStatusMessage } from "../media/imagePicker";

type DetailError = "none" | "notFound" | "offline" | "generic";
type ViewAccessibilityRole = NonNullable<ComponentProps<typeof View>["accessibilityRole"]>;

/** React Native's current role union omits web's valid listitem role. Keep it web-only. */
const webListItemProps: { accessibilityRole?: ViewAccessibilityRole } = Platform.OS === "web"
  ? { accessibilityRole: "listitem" as unknown as ViewAccessibilityRole }
  : {};

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
  const [imageBusy, setImageBusy] = useState(false);
  const [imageMessage, setImageMessage] = useState<string | null>(null);
  const [pendingCover, setPendingCover] = useState<{ uri: string; mimeType: string } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const mounted = useRef(true);
  const loadRequestId = useRef(0);
  const ratingRequestId = useRef(0);

  const load = useCallback(async () => {
    if (!id) { setRecipe(null); setLoading(false); setError("notFound"); return; }
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

  const loadCoverImage = useCallback<CoverImageLoader>(
    ({ recipeId: coverId, etag, signal }) => catalog.getCoverImage(coverId, { etag, signal }),
    [catalog],
  );

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; loadRequestId.current += 1; ratingRequestId.current += 1; };
  }, [load]);

  const uploadSelectedCover = async (selected: { uri: string; mimeType: string }) => {
    if (!id || !recipe || imageBusy) return;
    setImageBusy(true);
    setImageMessage(null);
    try {
      const blob = await blobFromPickerUri(selected.uri, selected.mimeType);
      const cover = await catalog.uploadCoverImage(id, blob);
      if (!mounted.current) return;
      setPendingCover(null);
      setRecipe((current) => (current?.id === id ? { ...current, coverImage: cover } : current));
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setPendingCover(selected);
      setImageMessage(coverImageErrorMessage(caught));
    } finally {
      if (mounted.current) setImageBusy(false);
    }
  };

  const handlePickCover = async () => {
    if (imageBusy) return;
    const result = await pickRecipeCoverImage();
    if (result.status === "cancelled") return;
    const message = pickerStatusMessage(result.status);
    if (message) {
      setImageMessage(message);
      return;
    }
    if (result.status === "selected") {
      await uploadSelectedCover(result);
    }
  };

  const handleRemoveCover = async () => {
    if (!id || !recipe?.coverImage || imageBusy) return;
    setConfirmRemove(false);
    setImageBusy(true);
    setImageMessage(null);
    try {
      await catalog.deleteCoverImage(id);
      if (!mounted.current) return;
      setRecipe((current) => (current?.id === id ? { ...current, coverImage: null } : current));
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setImageMessage(coverImageErrorMessage(caught));
    } finally {
      if (mounted.current) setImageBusy(false);
    }
  };

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
      setRecipe((current) => current?.id === id ? { ...current, rating: priorRating } : current);
      if (caught instanceof ApiUnauthorizedError) { onUnauthorized(); return; }
      setRatingRetry(value);
    } finally {
      if (mounted.current && requestId === ratingRequestId.current) setSavingRating(false);
    }
  };

  const errorContent = error === "notFound"
    ? <ErrorState title="We couldn't find that recipe." action={<Button label="Try again" onPress={() => void load()} />} />
    : error === "offline"
      ? <><OfflineBanner message={"You\u2019re offline. Check your connection and try again."} /><Button label="Try again" onPress={() => void load()} /></>
      : <ErrorState title="We couldn't load this recipe. Please try again." action={<Button label="Try again" onPress={() => void load()} />} />;

  return <Screen><Button label="Back to list" variant="secondary" onPress={onBack} />
    {loading ? <LoadingState label="Loading recipe" /> : error !== "none" && !recipe ? errorContent : recipe ? <View style={styles.detail}>
      <View testID="recipe-detail-media" style={styles.mediaSlot}>
        <RecipeMedia
          recipeId={recipe.id}
          title={recipe.title}
          tags={recipe.tags}
          coverImage={recipe.coverImage}
          loadCoverImage={loadCoverImage}
        />
      </View>
      <View style={styles.coverActions}>
        <Button
          label={recipe.coverImage ? "Replace cover image" : "Add cover image"}
          variant="secondary"
          loading={imageBusy}
          disabled={imageBusy}
          onPress={() => void handlePickCover()}
        />
        {recipe.coverImage ? (
          <Button
            label="Remove cover image"
            variant="quiet"
            disabled={imageBusy}
            onPress={() => setConfirmRemove(true)}
          />
        ) : null}
        {pendingCover ? (
          <Button
            label="Try image upload again"
            variant="secondary"
            loading={imageBusy}
            onPress={() => void uploadSelectedCover(pendingCover)}
          />
        ) : null}
        {imageMessage ? <InlineNotice tone="error" message={imageMessage} /> : null}
      </View>
      <ConfirmDialog
        visible={confirmRemove}
        title="Remove cover image?"
        description="The generated placeholder will be shown instead."
        onConfirm={() => void handleRemoveCover()}
        onCancel={() => setConfirmRemove(false)}
      />
      <PageHeader title={recipe.title} subtitle={[recipe.servings ? `Serves ${recipe.servings}` : null, recipe.totalMinutes ? `${recipe.totalMinutes} min` : null].filter(Boolean).join(" · ") || undefined} />
      <Section title="Rating"><Text>{recipe.rating ? `${recipe.rating} out of 5` : "Not rated"}</Text><RatingControl value={recipe.rating ?? 0} onChange={(value) => void setRating(value)} disabled={savingRating} />{ratingRetry ? <View style={styles.ratingError}><InlineNotice tone="error" message="We couldn't save your rating." /><Button label="Try rating again" variant="secondary" onPress={() => void setRating(ratingRetry)} /></View> : null}</Section>
      <View testID="recipe-detail-columns" style={styles.columns}>
        <Section title="Ingredients" accessibilityRole="list" accessibilityLabel="Ingredients" style={styles.ingredients}>{recipe.ingredients.length ? recipe.ingredients.map((ingredient, index) => <View key={`${ingredient.rawText}-${index}`} {...webListItemProps} accessibilityLabel={ingredient.rawText}><Text style={styles.listItem}>{"\u2022"} {ingredient.rawText}</Text></View>) : <Text>None listed.</Text>}</Section>
        <Section title="Instructions" accessibilityRole="list" accessibilityLabel="Instructions" style={styles.instructions}>{recipe.instructions.length ? recipe.instructions.map((step, index) => <View key={`${index}-${step.slice(0, 24)}`} {...webListItemProps}><Text style={styles.step}>{index + 1}. {step}</Text></View>) : <Text>None listed.</Text>}</Section>
      </View>
    </View> : null}
  </Screen>;
}

const styles = StyleSheet.create({ detail: { gap: 16 }, mediaSlot: { minHeight: 280, width: "100%" }, coverActions: { gap: 8 }, columns: { flexDirection: "row", flexWrap: "wrap", gap: 24 }, ingredients: { flexGrow: 1, flexBasis: 280 }, instructions: { flexGrow: 2, flexBasis: 520 }, listItem: { marginBottom: 8 }, step: { marginBottom: 12, lineHeight: 24 }, ratingError: { gap: 8 } });
