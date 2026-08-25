import { useCallback, useEffect, useRef, useState, type ComponentProps } from "react";
import { Platform, StyleSheet, Text, TextInput, View } from "react-native";

import { ApiError, ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, Recipe, RecipeCreateIngredient, RecipePatch } from "../api/catalog";
import { Button, ConfirmDialog, ErrorState, Field, InlineNotice, LoadingState, OfflineBanner, RatingControl, RecipeMedia, Screen, Section } from "../components";
import { ServingStepper } from "../components/ServingStepper";
import type { CoverImageLoader } from "../components/AuthenticatedRecipeImage";
import { blobFromPickerUri, coverImageErrorMessage, pickRecipeCoverImage, pickerStatusMessage } from "../media/imagePicker";
import { useTheme } from "../theme/ThemeProvider";
import { scaleFactor, scaleIngredients, type ServingStepperValue } from "../utils/ingredientScale";
import { parseRecipeLines } from "../utils/recipeFingerprint";
import { webDataset } from "../utils/webDataset";
import { PERSONAL_NOTES_MAX_LENGTH, RecipeFormFields } from "./RecipeFormFields";

type DetailError = "none" | "notFound" | "offline" | "generic";
type ViewAccessibilityRole = NonNullable<ComponentProps<typeof View>["accessibilityRole"]>;
type FormErrors = Partial<Record<"title" | "ingredients" | "instructions" | "notes", string>>;

/** React Native's current role union omits web's valid listitem role. Keep it web-only. */
const webListItemProps: { accessibilityRole?: ViewAccessibilityRole } = Platform.OS === "web"
  ? { accessibilityRole: "listitem" as unknown as ViewAccessibilityRole }
  : {};

export type RecipeDetailScreenProps = {
  recipeId: unknown;
  catalog: ReturnType<typeof createCatalogApi>;
  onBack(): void;
  onUnauthorized(): void;
  onStartCooking?: () => void;
};

function defaultServingValue(baseServings: number | null | undefined): ServingStepperValue {
  if (baseServings != null && baseServings > 0) {
    return { kind: "servings", servings: baseServings };
  }
  return { kind: "multiplier", multiplier: 1 };
}

function routeRecipeId(value: unknown): string | null {
  return typeof value === "string" && value.trim() && !/\s/.test(value) ? value.trim() : null;
}

function isOfflineError(error: unknown): boolean {
  return error instanceof ApiNetworkError || (typeof error === "object" && error !== null && ((error as { code?: unknown }).code === "ERR_NETWORK" || (error as { code?: unknown }).code === "NETWORK_ERROR"));
}

function isNotFoundError(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { status?: unknown }).status === 404;
}

function mapSaveError(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) {
    return "Some fields are invalid. Check your recipe and try again.";
  }
  return "We couldn't save your changes. Please try again.";
}

function formatLastCookedAt(value: string | null): string {
  if (!value) return "Not cooked yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Last cooked date unavailable";
  return `Last cooked ${date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}`;
}

function ingredientsPayload(recipe: Recipe, ingredientsText: string): RecipeCreateIngredient[] {
  const lines = parseRecipeLines(ingredientsText);
  const original = recipe.ingredients;
  if (lines.length === original.length && lines.every((line, index) => line === original[index]?.rawText)) {
    return original.map(({ rawText, name, canonicalName, quantity, unit }) => ({
      rawText,
      name,
      canonicalName,
      quantity,
      unit,
    }));
  }
  return lines.map((rawText) => ({ rawText, name: rawText, canonicalName: rawText }));
}

export function RecipeDetailScreen({ recipeId, catalog, onBack, onUnauthorized, onStartCooking }: RecipeDetailScreenProps) {
  const { theme } = useTheme();
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
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [ingredientsText, setIngredientsText] = useState("");
  const [instructionsText, setInstructionsText] = useState("");
  const [personalNotes, setPersonalNotes] = useState("");
  const [formErrors, setFormErrors] = useState<FormErrors>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [favoriteBusy, setFavoriteBusy] = useState(false);
  const [cookedBusy, setCookedBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [servingValue, setServingValue] = useState<ServingStepperValue>({ kind: "multiplier", multiplier: 1 });
  const mounted = useRef(true);
  const loadRequestId = useRef(0);
  const ratingRequestId = useRef(0);
  const saveInFlight = useRef(false);
  const deleteInFlight = useRef(false);

  const load = useCallback(async () => {
    if (!id) { setRecipe(null); setLoading(false); setError("notFound"); return; }
    const requestId = ++loadRequestId.current;
    setLoading(true); setError("none"); setRecipe(null); setRatingRetry(null); setEditing(false); setDeleteOpen(false);
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

  useEffect(() => {
    if (!recipe) return;
    setServingValue(defaultServingValue(recipe.servings));
  }, [recipe?.id]);

  const beginEdit = () => {
    if (!recipe) return;
    setTitle(recipe.title);
    setIngredientsText(recipe.ingredients.map((ingredient) => ingredient.rawText).join("\n"));
    setInstructionsText(recipe.instructions.join("\n"));
    setPersonalNotes(recipe.personalNotes ?? "");
    setFormErrors({});
    setSaveError(null);
    setEditing(true);
    setDeleteOpen(false);
  };

  const cancelEdit = () => {
    setEditing(false);
    setFormErrors({});
    setSaveError(null);
  };

  const saveEdits = async () => {
    if (!id || !recipe || saveInFlight.current) return;
    const trimmedTitle = title.trim();
    const instructions = parseRecipeLines(instructionsText);
    const notes = personalNotes.trim();
    const errors: FormErrors = {
      title: trimmedTitle ? undefined : "Title is required.",
      ingredients: parseRecipeLines(ingredientsText).length > 0 ? undefined : "Add at least one ingredient.",
      instructions: instructions.length > 0 ? undefined : "Add at least one instruction.",
      notes: notes.length > PERSONAL_NOTES_MAX_LENGTH ? `Personal notes must be ${PERSONAL_NOTES_MAX_LENGTH} characters or fewer.` : undefined,
    };
    setFormErrors(errors);
    if (errors.title || errors.ingredients || errors.instructions || errors.notes) return;
    const body: RecipePatch = {
      title: trimmedTitle,
      ingredients: ingredientsPayload(recipe, ingredientsText),
      instructions,
      personalNotes: notes.length ? notes : null,
    };
    saveInFlight.current = true;
    setSaving(true);
    setSaveError(null);
    try {
      const next = await catalog.patchRecipe(id, body);
      if (!mounted.current) return;
      setRecipe(next);
      setEditing(false);
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiUnauthorizedError) { onUnauthorized(); return; }
      setSaveError(mapSaveError(caught));
    } finally {
      saveInFlight.current = false;
      if (mounted.current) setSaving(false);
    }
  };

  const toggleFavorite = async () => {
    if (!id || !recipe || favoriteBusy) return;
    const nextFavorite = !recipe.favorite;
    const prior = recipe.favorite;
    setFavoriteBusy(true);
    setActionError(null);
    setRecipe((current) => (current?.id === id ? { ...current, favorite: nextFavorite } : current));
    try {
      const next = await catalog.patchRecipe(id, { favorite: nextFavorite });
      if (!mounted.current) return;
      setRecipe(next);
    } catch (caught) {
      if (!mounted.current) return;
      setRecipe((current) => (current?.id === id ? { ...current, favorite: prior } : current));
      if (caught instanceof ApiUnauthorizedError) { onUnauthorized(); return; }
      setActionError("We couldn't update favorites. Please try again.");
    } finally {
      if (mounted.current) setFavoriteBusy(false);
    }
  };

  const markCooked = async () => {
    if (!id || !recipe || cookedBusy) return;
    setCookedBusy(true);
    setActionError(null);
    try {
      const next = await catalog.markRecipeCooked(id);
      if (!mounted.current) return;
      setRecipe(next);
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiUnauthorizedError) { onUnauthorized(); return; }
      setActionError("We couldn't mark this recipe as cooked. Please try again.");
    } finally {
      if (mounted.current) setCookedBusy(false);
    }
  };

  const deleteRecipe = async () => {
    if (!id || !recipe || deleteInFlight.current || deleteConfirmation !== recipe.title) return;
    deleteInFlight.current = true;
    setDeleting(true);
    setDeleteError(null);
    try {
      await catalog.deleteRecipe(id);
      if (!mounted.current) return;
      onBack();
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiUnauthorizedError) { onUnauthorized(); return; }
      if (isNotFoundError(caught)) { onBack(); return; }
      setDeleteError("We couldn't delete this recipe. Please try again.");
    } finally {
      deleteInFlight.current = false;
      if (mounted.current) setDeleting(false);
    }
  };

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

  const metadata = [recipe?.servings ? `Serves ${recipe.servings}` : null, recipe?.totalMinutes ? `${recipe.totalMinutes} min` : null].filter(Boolean).join(" · ");
  const canDelete = Boolean(recipe && deleteConfirmation === recipe.title && !deleting);
  const ingredientScaleFactor = recipe
    ? scaleFactor({
      baseServings: recipe.servings,
      selectedServings: servingValue.kind === "servings" ? servingValue.servings : null,
      multiplier: servingValue.kind === "multiplier" ? servingValue.multiplier : 1,
    })
    : 1;
  const displayedIngredients = recipe ? scaleIngredients(recipe.ingredients, ingredientScaleFactor) : [];
  const printRecipe = () => {
    if (Platform.OS === "web" && typeof window !== "undefined") {
      window.print();
    }
  };

  return <Screen><Button label="Back to list" variant="quiet" onPress={onBack} />
    {loading ? <LoadingState label="Loading recipe" /> : error !== "none" && !recipe ? errorContent : recipe ? <View {...webDataset({ printRoot: true })} style={styles.detail}>
      <View testID="recipe-detail-media" style={styles.mediaSlot}>
        <RecipeMedia
          recipeId={recipe.id}
          title={recipe.title}
          tags={recipe.tags}
          coverImage={recipe.coverImage}
          loadCoverImage={loadCoverImage}
        />
      </View>
      <View style={styles.coverActions} {...webDataset({ printHide: true })}>
        <Button
          label={recipe.coverImage ? "Replace cover image" : "Add cover image"}
          variant="secondary"
          loading={imageBusy}
          disabled={imageBusy || editing || deleting}
          onPress={() => void handlePickCover()}
        />
        {recipe.coverImage ? (
          <Button
            label="Remove cover image"
            variant="quiet"
            disabled={imageBusy || editing || deleting}
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
      {editing ? (
        <>
          <RecipeFormFields
            title={title}
            onTitleChange={(value) => { setTitle(value); setFormErrors((current) => ({ ...current, title: undefined })); }}
            titleError={formErrors.title}
            ingredientsText={ingredientsText}
            onIngredientsChange={(value) => { setIngredientsText(value); setFormErrors((current) => ({ ...current, ingredients: undefined })); }}
            ingredientsError={formErrors.ingredients}
            instructionsText={instructionsText}
            onInstructionsChange={(value) => { setInstructionsText(value); setFormErrors((current) => ({ ...current, instructions: undefined })); }}
            instructionsError={formErrors.instructions}
            personalNotes={personalNotes}
            onPersonalNotesChange={(value) => { setPersonalNotes(value); setFormErrors((current) => ({ ...current, notes: undefined })); }}
            notesError={formErrors.notes}
            disabled={saving}
            onSubmitEditing={() => void saveEdits()}
          />
          {saveError ? <InlineNotice tone="error" message={saveError} /> : null}
          <View style={styles.actions}>
            <Button label="Save changes" loading={saving} disabled={saving} onPress={() => void saveEdits()} />
            <Button label="Cancel" variant="secondary" disabled={saving} onPress={cancelEdit} />
          </View>
        </>
      ) : (
        <>
          <Text accessibilityRole="header" style={[styles.heroTitle, { color: theme.colors.text, fontFamily: theme.type.fontFamily.heading }]}>{recipe.title}</Text>
          {metadata ? <Text style={[styles.heroMeta, { color: theme.colors.mutedText }]}>{metadata}</Text> : null}
          <Text style={[styles.heroMeta, { color: theme.colors.mutedText }]}>{formatLastCookedAt(recipe.lastCookedAt)}</Text>
          <View style={styles.actions} {...webDataset({ printHide: true })}>
            <Button
              label={recipe.favorite ? `Remove ${recipe.title} from favorites` : `Add ${recipe.title} to favorites`}
              variant={recipe.favorite ? "secondary" : "quiet"}
              loading={favoriteBusy}
              disabled={favoriteBusy || cookedBusy}
              onPress={() => void toggleFavorite()}
            />
            <Button label="Mark as cooked" variant="secondary" loading={cookedBusy} disabled={cookedBusy || favoriteBusy} onPress={() => void markCooked()} />
            {onStartCooking ? <Button label="Start cooking" onPress={onStartCooking} /> : null}
            {Platform.OS === "web" ? <Button label="Print" variant="secondary" onPress={printRecipe} /> : null}
            <Button label="Edit recipe" variant="secondary" onPress={beginEdit} />
            <Button label="Delete recipe" variant="danger" onPress={() => { setDeleteOpen(true); setDeleteConfirmation(""); setDeleteError(null); }} />
          </View>
          {actionError ? <InlineNotice tone="error" message={actionError} /> : null}
          <Section title="Personal notes" {...webDataset({ printHide: true })}>
            <Text style={{ color: theme.colors.text }}>{recipe.personalNotes?.trim() ? recipe.personalNotes : "No personal notes yet."}</Text>
          </Section>
          <Section title="Rating" {...webDataset({ printHide: true })}><Text>{recipe.rating ? `${recipe.rating} out of 5` : "Not rated"}</Text><RatingControl value={recipe.rating ?? 0} onChange={(value) => void setRating(value)} disabled={savingRating} />{ratingRetry ? <View style={styles.ratingError}><InlineNotice tone="error" message="We couldn't save your rating." /><Button label="Try rating again" variant="secondary" onPress={() => void setRating(ratingRetry)} /></View> : null}</Section>
          <View {...webDataset({ printHide: true })}>
            <ServingStepper servings={recipe.servings} value={servingValue} onChange={setServingValue} />
          </View>
          <View testID="recipe-detail-columns" style={styles.columns}>
            <Section title="Ingredients" accessibilityRole="list" accessibilityLabel="Ingredients" style={[styles.ingredients, { borderLeftColor: theme.colors.accent }]}>{recipe.ingredients.length ? recipe.ingredients.map((ingredient, index) => { const displayText = displayedIngredients[index] ?? ingredient.rawText; return <View key={`${ingredient.rawText}-${index}`} {...webListItemProps} accessibilityLabel={displayText}><Text style={styles.listItem}>{"\u2022"} {displayText}</Text></View>; }) : <Text>None listed.</Text>}</Section>
            <Section title="Instructions" accessibilityRole="list" accessibilityLabel="Instructions" style={styles.instructions}>{recipe.instructions.length ? recipe.instructions.map((step, index) => <View key={`${index}-${step.slice(0, 24)}`} {...webListItemProps} style={styles.stepRow}><Text style={[styles.stepNumeral, { color: theme.colors.accent, fontFamily: theme.type.fontFamily.heading }]}>{index + 1}</Text><Text style={styles.step}>{step}</Text></View>) : <Text>None listed.</Text>}</Section>
          </View>
          {deleteOpen ? (
            <Section title="Delete recipe">
              <Text style={[styles.deleteCopy, { color: theme.colors.mutedText }]}>This permanently deletes the recipe. Type {recipe.title} exactly to continue.</Text>
              <Field
                label={`Type ${recipe.title} to confirm`}
                error={deleteConfirmation.length > 0 && deleteConfirmation !== recipe.title ? `Enter ${recipe.title} exactly.` : undefined}
                control={<TextInput value={deleteConfirmation} onChangeText={setDeleteConfirmation} autoCorrect={false} editable={!deleting} />}
              />
              {deleteError ? <InlineNotice tone="error" message={deleteError} /> : null}
              <View style={styles.actions}>
                <Button label="Delete this recipe" variant="danger" disabled={!canDelete} loading={deleting} onPress={() => void deleteRecipe()} />
                <Button label="Cancel deletion" variant="secondary" disabled={deleting} onPress={() => { setDeleteOpen(false); setDeleteConfirmation(""); setDeleteError(null); }} />
              </View>
            </Section>
          ) : null}
        </>
      )}
    </View> : null}
  </Screen>;
}

const styles = StyleSheet.create({
  detail: { gap: 16 },
  mediaSlot: { minHeight: 320, width: "100%", borderRadius: 16, overflow: "hidden" },
  coverActions: { flexDirection: "row", flexWrap: "wrap", gap: 8, alignItems: "center" },
  heroTitle: { fontSize: 32, fontWeight: "700" },
  heroMeta: { fontSize: 15 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8, alignItems: "center" },
  columns: { flexDirection: "row", flexWrap: "wrap", gap: 24 },
  ingredients: { flexGrow: 1, flexBasis: 280, borderLeftWidth: 3, paddingLeft: 16 },
  instructions: { flexGrow: 2, flexBasis: 520 },
  listItem: { marginBottom: 8 },
  stepRow: { flexDirection: "row", gap: 12, marginBottom: 12, alignItems: "flex-start" },
  stepNumeral: { fontSize: 28, fontWeight: "700", minWidth: 28, lineHeight: 32 },
  step: { flex: 1, lineHeight: 24, paddingTop: 4 },
  ratingError: { gap: 8 },
  deleteCopy: { marginBottom: 16, lineHeight: 20 },
});
