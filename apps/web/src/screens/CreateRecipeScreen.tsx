import { useRef, useState } from "react";
import {
  TextInput,
  StyleSheet,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi } from "../api/catalog";
import { Button, Field, InlineNotice, PageHeader, Screen, TextArea } from "../components";
import type { LayoutMode } from "../navigation/types";
import { useTheme } from "../theme/ThemeProvider";
import {
  resolveIdempotencySession,
  type IdempotencySession,
} from "../utils/idempotencySession";
import { fingerprintRecipeCreate, parseRecipeLines } from "../utils/recipeFingerprint";

export type CreateRecipeScreenProps = {
  catalog: ReturnType<typeof createCatalogApi>;
  onCreated(recipeId: string): void;
  onBack(): void;
  onUnauthorized(): void;
  layoutMode?: LayoutMode;
};

export function CreateRecipeScreen({
  catalog,
  onCreated,
  onBack,
  onUnauthorized,
  layoutMode = "medium",
}: CreateRecipeScreenProps) {
  const [title, setTitle] = useState("");
  const [ingredientsText, setIngredientsText] = useState("");
  const [instructionsText, setInstructionsText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formErrors, setFormErrors] = useState<Partial<Record<"title" | "ingredients" | "instructions", string>>>({});
  const [requestError, setRequestError] = useState<string | null>(null);
  const idempotencyRef = useRef<IdempotencySession | null>(null);
  const submissionInFlightRef = useRef(false);
  const insets = useSafeAreaInsets();
  const { theme } = useTheme();

  const submit = async () => {
    if (submissionInFlightRef.current) return;
    const trimmedTitle = title.trim();
    const normalizedIngredients = parseRecipeLines(ingredientsText);
    const normalizedInstructions = parseRecipeLines(instructionsText);
    const nextFormErrors = {
      title: trimmedTitle ? undefined : "Title is required.",
      ingredients: normalizedIngredients.length > 0 ? undefined : "Add at least one ingredient.",
      instructions: normalizedInstructions.length > 0 ? undefined : "Add at least one instruction.",
    };
    setFormErrors(nextFormErrors);
    if (nextFormErrors.title || nextFormErrors.ingredients || nextFormErrors.instructions) {
      return;
    }

    const ingredients = normalizedIngredients.map((rawText) => ({
      rawText,
      name: rawText,
    }));
    const fingerprint = fingerprintRecipeCreate({
      title: trimmedTitle,
      ingredients: normalizedIngredients,
      instructions: normalizedInstructions,
    });

    submissionInFlightRef.current = true;
    setSubmitting(true);
    setRequestError(null);
    try {
      const session = resolveIdempotencySession(idempotencyRef.current, fingerprint);
      idempotencyRef.current = session;
      const recipe = await catalog.createRecipe(
        { title: trimmedTitle, ingredients, instructions: normalizedInstructions },
        session.key,
      );
      idempotencyRef.current = null;
      onCreated(recipe.id);
    } catch (err) {
      if (err instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setRequestError("We couldn't create your recipe. Please try again.");
    } finally {
      submissionInFlightRef.current = false;
      setSubmitting(false);
    }
  };

  const submitButton = (testID: string) => (
    <Button
      testID={testID}
      label="Create recipe"
      loading={submitting}
      disabled={submitting}
      onPress={() => void submit()}
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
            control={<TextInput value={title} onChangeText={setTitle} placeholder="Recipe title" placeholderTextColor={theme.colors.mutedText} returnKeyType="done" onSubmitEditing={() => void submit()} />}
          />
          <TextArea
            label="Ingredients"
            hint="One ingredient per line, for example: 2 cups tomatoes"
            error={formErrors.ingredients}
            value={ingredientsText}
            onChangeText={setIngredientsText}
            placeholder={"2 cups tomatoes\n1 tsp salt"}
            placeholderTextColor={theme.colors.mutedText}
            numberOfLines={6}
          />
          <TextArea
            label="Instructions"
            hint="One step per line, for example: Simmer for 20 minutes."
            error={formErrors.instructions}
            value={instructionsText}
            onChangeText={setInstructionsText}
            placeholder={"Chop the vegetables.\nSimmer until tender."}
            placeholderTextColor={theme.colors.mutedText}
            numberOfLines={8}
            returnKeyType="done"
            blurOnSubmit
            onSubmitEditing={() => void submit()}
          />
          {requestError ? <InlineNotice tone="error" message={requestError} /> : null}
        </View>
      </Screen>
      {compact ? (
        <View testID="create-recipe-sticky-submit" style={[styles.stickySubmit, { backgroundColor: theme.colors.elevatedSurface, borderColor: theme.colors.border, paddingBottom: insets.bottom + theme.spacing.sm, paddingHorizontal: theme.spacing.md, paddingTop: theme.spacing.sm }]}>
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
});
