import { useEffect, useRef, useState } from "react";
import { Image, TextInput, StyleSheet, View, Text } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ApiError, ApiUnauthorizedError } from "../api/client";
import type { RecipeCreate, RecipeCreateIngredient } from "../api/catalog";
import type { createCatalogApi } from "../api/catalog";
import type { createIngestionApi, ImportReviewDraft } from "../api/ingestion";
import { Button, Field, InlineNotice, PageHeader, Screen, TextArea } from "../components";
import {
  blobFromPickerUri,
  pickRecipeCoverImage,
  pickerStatusMessage,
  type PickedCover,
} from "../media/imagePicker";
import type { LayoutMode } from "../navigation/types";
import { useTheme } from "../theme/ThemeProvider";
import {
  resolveIdempotencySession,
  type IdempotencySession,
  type ReviewedCreateAttempt,
} from "../utils/idempotencySession";
import {
  fingerprintIngredientNormalization,
  fingerprintRecipeCreate,
  parseRecipeLines,
} from "../utils/recipeFingerprint";

export type CreateRecipeScreenProps = {
  catalog: ReturnType<typeof createCatalogApi>;
  ingestion: ReturnType<typeof createIngestionApi>;
  onCreated(recipeId: string): void;
  onBack(): void;
  onUnauthorized(): void;
  layoutMode?: LayoutMode;
  importJobId?: string | null;
};

type FormErrors = Partial<Record<"title" | "ingredients" | "instructions" | "quantity", string>>;

type ExtractedRecipeMetadata = Pick<
  ImportReviewDraft,
  "sourceUrl" | "servings" | "prepMinutes" | "cookMinutes" | "totalMinutes" | "tags"
>;

function completeQuantity(value: string): number | null | undefined {
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  if (!/^\d+(\.\d+)?$/.test(trimmed)) return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function quantityDraftFromIngredient(ingredient: RecipeCreateIngredient): string {
  return ingredient.quantity === null || ingredient.quantity === undefined ? "" : String(ingredient.quantity);
}

function mapRequestError(error: unknown, phase: "review" | "save"): string | null {
  if (error instanceof ApiUnauthorizedError) {
    return null;
  }
  if (error instanceof ApiError) {
    switch (error.status) {
      case 409:
        return "This recipe was already submitted with different content. Change something and try again.";
      case 422:
        return "Some fields are invalid. Check your recipe and try again.";
      case 429:
        return "Too many requests. Please wait a moment and try again.";
      case 503:
        return "The service is temporarily unavailable. Please try again later.";
      default:
        break;
    }
  }
  return phase === "review"
    ? "We couldn't review your recipe. Please try again."
    : "We couldn't create your recipe. Please try again.";
}

function buildReviewedPayload(
  title: string,
  instructions: string[],
  ingredients: RecipeCreateIngredient[],
  metadata: ExtractedRecipeMetadata | null,
): RecipeCreate {
  if (!metadata) {
    return {
      title,
      ingredients,
      instructions,
      tags: [],
    };
  }
  return {
    title,
    ingredients,
    instructions,
    sourceUrl: metadata.sourceUrl,
    servings: metadata.servings,
    prepMinutes: metadata.prepMinutes,
    cookMinutes: metadata.cookMinutes,
    totalMinutes: metadata.totalMinutes,
    tags: metadata.tags,
  };
}

function metadataFromDraft(draft: ImportReviewDraft): ExtractedRecipeMetadata {
  return {
    sourceUrl: draft.sourceUrl,
    servings: draft.servings,
    prepMinutes: draft.prepMinutes,
    cookMinutes: draft.cookMinutes,
    totalMinutes: draft.totalMinutes,
    tags: draft.tags,
  };
}

function withCatalogSession(
  attempt: ReviewedCreateAttempt,
  payload: RecipeCreate,
): ReviewedCreateAttempt {
  const catalogSession = resolveIdempotencySession(
    attempt.catalogSession,
    fingerprintRecipeCreate(payload),
  );
  return { ...attempt, reviewedPayload: payload, catalogSession };
}

export function CreateRecipeScreen({
  catalog,
  ingestion,
  onCreated,
  onBack,
  onUnauthorized,
  layoutMode = "medium",
  importJobId = null,
}: CreateRecipeScreenProps) {
  const [title, setTitle] = useState("");
  const [ingredientsText, setIngredientsText] = useState("");
  const [instructionsText, setInstructionsText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formErrors, setFormErrors] = useState<FormErrors>({});
  const [requestError, setRequestError] = useState<string | null>(null);
  const [draftNotice, setDraftNotice] = useState<string | null>(null);
  const [draftLoading, setDraftLoading] = useState(Boolean(importJobId));
  const [extractedMetadata, setExtractedMetadata] = useState<ExtractedRecipeMetadata | null>(null);
  const [reviewedAttempt, setReviewedAttempt] = useState<ReviewedCreateAttempt | null>(null);
  const [quantityDrafts, setQuantityDrafts] = useState<string[]>([]);
  const [cover, setCover] = useState<Extract<PickedCover, { status: "selected" }> | null>(null);
  const [coverMessage, setCoverMessage] = useState<string | null>(null);
  const [createdRecipeId, setCreatedRecipeId] = useState<string | null>(null);
  const [imageUploading, setImageUploading] = useState(false);
  const normalizationSessionRef = useRef<IdempotencySession | null>(null);
  const submissionInFlightRef = useRef(false);
  const formDirtyRef = useRef(false);
  const titleRef = useRef(title);
  const ingredientsTextRef = useRef(ingredientsText);
  const instructionsTextRef = useRef(instructionsText);
  titleRef.current = title;
  ingredientsTextRef.current = ingredientsText;
  instructionsTextRef.current = instructionsText;
  const insets = useSafeAreaInsets();
  const { theme } = useTheme();

  useEffect(() => {
    if (!importJobId) {
      setDraftLoading(false);
      return;
    }
    let cancelled = false;
    setDraftLoading(true);
    void ingestion.getImportDraft(importJobId).then(
      (draft) => {
        if (cancelled) {
          return;
        }
        if (formDirtyRef.current) {
          setDraftNotice("We didn't replace your edits with the extracted recipe.");
          setDraftLoading(false);
          return;
        }
        setTitle(draft.title ?? "");
        setIngredientsText(draft.ingredients.join("\n"));
        setInstructionsText(draft.instructions.join("\n"));
        setExtractedMetadata(metadataFromDraft(draft));
        setDraftNotice("We loaded the extracted recipe. Check it, then review and save.");
        setRequestError(null);
        setDraftLoading(false);
      },
      (error: unknown) => {
        if (cancelled) {
          return;
        }
        setDraftLoading(false);
        if (error instanceof ApiUnauthorizedError) {
          onUnauthorized();
          return;
        }
        setDraftNotice(null);
        setRequestError("We couldn't load the extracted recipe. You can still enter it here.");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [importJobId, ingestion, onUnauthorized]);

  const isReviewed = reviewedAttempt !== null;
  const primaryLabel = isReviewed ? "Save recipe" : "Review recipe";

  const validateForm = (): {
    trimmedTitle: string;
    normalizedIngredients: string[];
    normalizedInstructions: string[];
    errors: FormErrors;
  } | null => {
    const trimmedTitle = title.trim();
    const normalizedIngredients = parseRecipeLines(ingredientsText);
    const normalizedInstructions = parseRecipeLines(instructionsText);
    const errors: FormErrors = {
      title: trimmedTitle ? undefined : "Title is required.",
      ingredients: normalizedIngredients.length > 0 ? undefined : "Add at least one ingredient.",
      instructions: normalizedInstructions.length > 0 ? undefined : "Add at least one instruction.",
    };
    setFormErrors(errors);
    if (errors.title || errors.ingredients || errors.instructions) {
      return null;
    }
    return { trimmedTitle, normalizedIngredients, normalizedInstructions, errors };
  };

  const discardReviewIfRawLinesChanged = (nextIngredientsText: string) => {
    if (!reviewedAttempt) {
      return;
    }
    const nextFingerprint = fingerprintIngredientNormalization(parseRecipeLines(nextIngredientsText));
    if (nextFingerprint !== reviewedAttempt.rawFingerprint) {
      setReviewedAttempt(null);
      setQuantityDrafts([]);
    }
  };

  const handleIngredientsChange = (value: string) => {
    formDirtyRef.current = true;
    setIngredientsText(value);
    setFormErrors((previous) => ({ ...previous, ingredients: undefined }));
    discardReviewIfRawLinesChanged(value);
  };

  const handleTitleChange = (value: string) => {
    formDirtyRef.current = true;
    setTitle(value);
    setFormErrors((previous) => ({ ...previous, title: undefined }));
    if (!reviewedAttempt) {
      return;
    }
    const trimmedTitle = value.trim();
    const normalizedInstructions = parseRecipeLines(instructionsText);
    const nextPayload = buildReviewedPayload(
      trimmedTitle,
      normalizedInstructions,
      reviewedAttempt.reviewedPayload.ingredients,
      extractedMetadata,
    );
    setReviewedAttempt(withCatalogSession(reviewedAttempt, nextPayload));
  };

  const handleInstructionsChange = (value: string) => {
    formDirtyRef.current = true;
    setInstructionsText(value);
    setFormErrors((previous) => ({ ...previous, instructions: undefined }));
    if (!reviewedAttempt) {
      return;
    }
    const normalizedInstructions = parseRecipeLines(value);
    const nextPayload = buildReviewedPayload(
      reviewedAttempt.reviewedPayload.title,
      normalizedInstructions,
      reviewedAttempt.reviewedPayload.ingredients,
      extractedMetadata,
    );
    setReviewedAttempt(withCatalogSession(reviewedAttempt, nextPayload));
  };

  const handleIngredientFieldChange = (
    index: number,
    field: "name" | "quantity" | "unit" | "canonicalName",
    value: string,
  ) => {
    if (!reviewedAttempt) {
      return;
    }
    const ingredients = reviewedAttempt.reviewedPayload.ingredients.map((ingredient, ingredientIndex) => {
      if (ingredientIndex !== index) {
        return ingredient;
      }
      if (field === "quantity") {
        setQuantityDrafts((previous) => {
          const next = [...previous];
          next[index] = value;
          return next;
        });
        setFormErrors((previous) => ({ ...previous, quantity: undefined }));
        const parsed = completeQuantity(value);
        if (parsed === undefined) {
          return ingredient;
        }
        return {
          ...ingredient,
          quantity: parsed,
        };
      }
      if (field === "unit") {
        const trimmed = value.trim();
        return {
          ...ingredient,
          unit: trimmed.length === 0 ? null : trimmed,
        };
      }
      return {
        ...ingredient,
        [field]: value,
      };
    });
    const nextPayload = buildReviewedPayload(
      reviewedAttempt.reviewedPayload.title,
      reviewedAttempt.reviewedPayload.instructions,
      ingredients,
      extractedMetadata,
    );
    setReviewedAttempt(withCatalogSession(reviewedAttempt, nextPayload));
  };

  const reviewRecipe = async () => {
    if (submissionInFlightRef.current || draftLoading) {
      return;
    }
    const validated = validateForm();
    if (!validated) {
      return;
    }

    const { normalizedIngredients } = validated;
    const rawFingerprint = fingerprintIngredientNormalization(normalizedIngredients);
    const normalizationSession = resolveIdempotencySession(
      normalizationSessionRef.current,
      rawFingerprint,
    );
    normalizationSessionRef.current = normalizationSession;

    submissionInFlightRef.current = true;
    setSubmitting(true);
    setRequestError(null);
    try {
      const normalized = await ingestion.normalizeIngredients(
        normalizedIngredients.map((rawText) => ({ rawText })),
        normalizationSession.key,
      );
      const currentRawFingerprint = fingerprintIngredientNormalization(
        parseRecipeLines(ingredientsTextRef.current),
      );
      if (currentRawFingerprint !== rawFingerprint) {
        return;
      }
      const currentTitle = titleRef.current.trim();
      const currentInstructions = parseRecipeLines(instructionsTextRef.current);
      if (!currentTitle || currentInstructions.length === 0) {
        return;
      }
      const reviewedIngredients = normalized.ingredients.map((ingredient) => ({
        rawText: ingredient.rawText,
        name: ingredient.name,
        canonicalName: ingredient.canonicalName,
        quantity: ingredient.quantity,
        unit: ingredient.unit,
      }));
      const reviewedPayload = buildReviewedPayload(
        currentTitle,
        currentInstructions,
        reviewedIngredients,
        extractedMetadata,
      );
      const catalogSession = resolveIdempotencySession(
        reviewedAttempt?.rawFingerprint === rawFingerprint ? reviewedAttempt.catalogSession : null,
        fingerprintRecipeCreate(reviewedPayload),
      );
      setQuantityDrafts(reviewedIngredients.map(quantityDraftFromIngredient));
      setReviewedAttempt({
        rawFingerprint,
        normalizationSession,
        reviewedPayload,
        catalogSession,
      });
    } catch (err) {
      if (err instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setRequestError(mapRequestError(err, "review"));
    } finally {
      submissionInFlightRef.current = false;
      setSubmitting(false);
    }
  };

  const saveRecipe = async () => {
    if (submissionInFlightRef.current || !reviewedAttempt || draftLoading || createdRecipeId) {
      return;
    }
    const validated = validateForm();
    if (!validated) {
      return;
    }
    if (quantityDrafts.some((draft) => completeQuantity(draft) === undefined)) {
      setFormErrors((previous) => ({ ...previous, quantity: "Quantity is invalid." }));
      return;
    }
    const ingredients = reviewedAttempt.reviewedPayload.ingredients.map((ingredient, index) => {
      const parsed = completeQuantity(quantityDrafts[index] ?? quantityDraftFromIngredient(ingredient));
      return { ...ingredient, quantity: parsed === undefined ? ingredient.quantity : parsed };
    });
    const payload = {
      ...reviewedAttempt.reviewedPayload,
      title: validated.trimmedTitle,
      instructions: validated.normalizedInstructions,
      ingredients,
    };
    const catalogSession = resolveIdempotencySession(
      reviewedAttempt.catalogSession,
      fingerprintRecipeCreate(payload),
    );
    setReviewedAttempt(withCatalogSession({ ...reviewedAttempt, catalogSession }, payload));

    submissionInFlightRef.current = true;
    setSubmitting(true);
    setRequestError(null);
    try {
      const recipe = await catalog.createRecipe(
        payload,
        catalogSession.key,
      );
      normalizationSessionRef.current = null;
      setReviewedAttempt(null);
      setQuantityDrafts([]);
      if (!cover) {
        onCreated(recipe.id);
        return;
      }
      setCreatedRecipeId(recipe.id);
      await uploadCover(recipe.id, cover);
    } catch (err) {
      if (err instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setRequestError(mapRequestError(err, "save"));
    } finally {
      submissionInFlightRef.current = false;
      setSubmitting(false);
    }
  };

  const uploadCover = async (
    recipeId: string,
    selected: Extract<PickedCover, { status: "selected" }>,
  ) => {
    setImageUploading(true);
    setCoverMessage(null);
    try {
      const blob = await blobFromPickerUri(selected.uri, selected.mimeType);
      await catalog.uploadCoverImage(recipeId, blob);
      onCreated(recipeId);
    } catch (err) {
      if (err instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setCoverMessage("Recipe saved; image upload failed.");
    } finally {
      setImageUploading(false);
    }
  };

  const handlePickCover = async () => {
    if (submitting || imageUploading || draftLoading) {
      return;
    }
    const result = await pickRecipeCoverImage();
    if (result.status === "cancelled") {
      return;
    }
    const message = pickerStatusMessage(result.status);
    if (message) {
      setCoverMessage(message);
      return;
    }
    if (result.status === "selected") {
      setCover(result);
      setCoverMessage(null);
    }
  };

  const handleRetryCover = async () => {
    if (!createdRecipeId || !cover || imageUploading) {
      return;
    }
    await uploadCover(createdRecipeId, cover);
  };

  const handlePrimaryAction = () => {
    if (draftLoading) {
      return;
    }
    if (isReviewed) {
      void saveRecipe();
      return;
    }
    void reviewRecipe();
  };

  const submitButton = (testID: string) => (
    <Button
      testID={testID}
      label={primaryLabel}
      loading={submitting}
      disabled={submitting || draftLoading || imageUploading || Boolean(createdRecipeId)}
      onPress={handlePrimaryAction}
    />
  );
  const compact = layoutMode === "compact";

  return (
    <View style={styles.root}>
      <Screen contentContainerStyle={compact ? { paddingBottom: insets.bottom + 104 } : undefined}>
        <PageHeader
          title="Create recipe"
          subtitle="Add the essentials now; you can refine the details later."
          actions={compact ? undefined : submitButton("create-recipe-header-submit")}
        />
        <Button label="Back to recipes" variant="secondary" onPress={onBack} />
        {draftLoading ? (
          <InlineNotice tone="info" message="Loading the extracted recipe…" />
        ) : draftNotice ? (
          <InlineNotice tone="info" message={draftNotice} />
        ) : null}
        <View style={styles.form}>
          <Field
            label="Title"
            hint="For example: Weeknight tomato soup"
            error={formErrors.title}
            control={
              <TextInput
                value={title}
                onChangeText={handleTitleChange}
                placeholder="Recipe title"
                placeholderTextColor={theme.colors.mutedText}
                returnKeyType="done"
                onSubmitEditing={handlePrimaryAction}
              />
            }
          />
          <TextArea
            label="Ingredients"
            hint="One ingredient per line, for example: 2 cups tomatoes"
            error={formErrors.ingredients}
            value={ingredientsText}
            onChangeText={handleIngredientsChange}
            placeholder={"2 cups tomatoes\n1 tsp salt"}
            placeholderTextColor={theme.colors.mutedText}
            numberOfLines={6}
          />
          {isReviewed ? (
            <View style={styles.reviewSection}>
              <Text style={[styles.reviewHeading, { color: theme.colors.text }]}>Review ingredients</Text>
              {reviewedAttempt.reviewedPayload.ingredients.map((ingredient, index) => (
                <View key={`${ingredient.rawText}-${index}`} style={styles.reviewIngredient}>
                  <Text style={[styles.reviewRawLine, { color: theme.colors.mutedText }]}>
                    {ingredient.rawText}
                  </Text>
                  <Field
                    label="Name"
                    control={
                      <TextInput
                        value={ingredient.name}
                        onChangeText={(value) => handleIngredientFieldChange(index, "name", value)}
                        placeholderTextColor={theme.colors.mutedText}
                      />
                    }
                  />
                  <Field
                    label="Quantity"
                    error={formErrors.quantity}
                    control={
                      <TextInput
                        value={quantityDrafts[index] ?? quantityDraftFromIngredient(ingredient)}
                        onChangeText={(value) => handleIngredientFieldChange(index, "quantity", value)}
                        placeholderTextColor={theme.colors.mutedText}
                        keyboardType="decimal-pad"
                      />
                    }
                  />
                  <Field
                    label="Unit"
                    control={
                      <TextInput
                        value={ingredient.unit ?? ""}
                        onChangeText={(value) => handleIngredientFieldChange(index, "unit", value)}
                        placeholderTextColor={theme.colors.mutedText}
                      />
                    }
                  />
                  <Field
                    label="Canonical"
                    control={
                      <TextInput
                        value={ingredient.canonicalName}
                        onChangeText={(value) => handleIngredientFieldChange(index, "canonicalName", value)}
                        placeholderTextColor={theme.colors.mutedText}
                      />
                    }
                  />
                </View>
              ))}
            </View>
          ) : null}
          <TextArea
            label="Instructions"
            hint="One step per line, for example: Simmer for 20 minutes."
            error={formErrors.instructions}
            value={instructionsText}
            onChangeText={handleInstructionsChange}
            placeholder={"Chop the vegetables.\nSimmer until tender."}
            placeholderTextColor={theme.colors.mutedText}
            numberOfLines={8}
            returnKeyType="done"
            blurOnSubmit
            onSubmitEditing={handlePrimaryAction}
          />
          {requestError ? <InlineNotice tone="error" message={requestError} /> : null}
          <View style={styles.coverSection}>
            <Button
              label={cover ? "Replace cover image" : "Add cover image"}
              variant="secondary"
              onPress={() => void handlePickCover()}
              disabled={submitting || imageUploading || draftLoading}
            />
            {cover ? (
              <Image
                testID="create-recipe-cover-preview"
                accessibilityLabel="Selected cover preview"
                source={{ uri: cover.uri }}
                style={styles.coverPreview}
              />
            ) : null}
            {coverMessage ? <InlineNotice tone="error" message={coverMessage} /> : null}
            {createdRecipeId && coverMessage?.startsWith("Recipe saved") ? (
              <Button
                label="Try image upload again"
                variant="secondary"
                loading={imageUploading}
                onPress={() => void handleRetryCover()}
              />
            ) : null}
          </View>
        </View>
      </Screen>
      {compact ? (
        <View
          testID="create-recipe-sticky-submit"
          style={[
            styles.stickySubmit,
            {
              backgroundColor: theme.colors.elevatedSurface,
              borderColor: theme.colors.border,
              paddingBottom: insets.bottom + theme.spacing.sm,
              paddingHorizontal: theme.spacing.md,
              paddingTop: theme.spacing.sm,
            },
          ]}
        >
          {submitButton("create-recipe-sticky-button")}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  form: { marginTop: 24 },
  stickySubmit: { position: "absolute", bottom: 0, left: 0, right: 0, borderTopWidth: 1 },
  reviewSection: { marginTop: 16, gap: 16 },
  reviewHeading: { fontSize: 16, fontWeight: "700" },
  reviewIngredient: { gap: 8 },
  reviewRawLine: { fontSize: 13, fontStyle: "italic" },
  coverSection: { marginTop: 16, gap: 12 },
  coverPreview: { width: "100%", height: 180, borderRadius: 16 },
});
