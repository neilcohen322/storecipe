import { useRef, useState } from "react";
import {
  TextInput,
  StyleSheet,
  View,
  Text,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ApiError, ApiUnauthorizedError } from "../api/client";
import type { RecipeCreate, RecipeCreateIngredient } from "../api/catalog";
import type { createCatalogApi } from "../api/catalog";
import type { createIngestionApi } from "../api/ingestion";
import { Button, Field, InlineNotice, PageHeader, Screen, TextArea } from "../components";
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
};

type FormErrors = Partial<Record<"title" | "ingredients" | "instructions", string>>;

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
): RecipeCreate {
  return {
    title,
    ingredients,
    instructions,
    tags: [],
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
}: CreateRecipeScreenProps) {
  const [title, setTitle] = useState("");
  const [ingredientsText, setIngredientsText] = useState("");
  const [instructionsText, setInstructionsText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formErrors, setFormErrors] = useState<FormErrors>({});
  const [requestError, setRequestError] = useState<string | null>(null);
  const [reviewedAttempt, setReviewedAttempt] = useState<ReviewedCreateAttempt | null>(null);
  const normalizationSessionRef = useRef<IdempotencySession | null>(null);
  const submissionInFlightRef = useRef(false);
  const insets = useSafeAreaInsets();
  const { theme } = useTheme();

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
    }
  };

  const handleIngredientsChange = (value: string) => {
    setIngredientsText(value);
    setFormErrors((previous) => ({ ...previous, ingredients: undefined }));
    discardReviewIfRawLinesChanged(value);
  };

  const handleTitleChange = (value: string) => {
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
    );
    setReviewedAttempt(withCatalogSession(reviewedAttempt, nextPayload));
  };

  const handleInstructionsChange = (value: string) => {
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
        const trimmed = value.trim();
        return {
          ...ingredient,
          quantity: trimmed.length === 0 ? null : Number(trimmed),
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
    );
    setReviewedAttempt(withCatalogSession(reviewedAttempt, nextPayload));
  };

  const reviewRecipe = async () => {
    if (submissionInFlightRef.current) {
      return;
    }
    const validated = validateForm();
    if (!validated) {
      return;
    }

    const { trimmedTitle, normalizedIngredients, normalizedInstructions } = validated;
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
      const reviewedPayload = buildReviewedPayload(
        trimmedTitle,
        normalizedInstructions,
        normalized.ingredients.map((ingredient) => ({
          rawText: ingredient.rawText,
          name: ingredient.name,
          canonicalName: ingredient.canonicalName,
          quantity: ingredient.quantity,
          unit: ingredient.unit,
        })),
      );
      const catalogSession = resolveIdempotencySession(
        reviewedAttempt?.rawFingerprint === rawFingerprint ? reviewedAttempt.catalogSession : null,
        fingerprintRecipeCreate(reviewedPayload),
      );
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
    if (submissionInFlightRef.current || !reviewedAttempt) {
      return;
    }
    const validated = validateForm();
    if (!validated) {
      return;
    }

    submissionInFlightRef.current = true;
    setSubmitting(true);
    setRequestError(null);
    try {
      const recipe = await catalog.createRecipe(
        reviewedAttempt.reviewedPayload,
        reviewedAttempt.catalogSession.key,
      );
      normalizationSessionRef.current = null;
      setReviewedAttempt(null);
      onCreated(recipe.id);
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

  const handlePrimaryAction = () => {
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
      disabled={submitting}
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
                    control={
                      <TextInput
                        value={ingredient.quantity === null || ingredient.quantity === undefined ? "" : String(ingredient.quantity)}
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
});
