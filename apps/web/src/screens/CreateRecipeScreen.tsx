import { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native";

import { ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi } from "../api/catalog";
import { colors, sharedStyles } from "../theme";
import { randomUuid } from "../utils/randomUuid";

export type CreateRecipeScreenProps = {
  catalog: ReturnType<typeof createCatalogApi>;
  onCreated(recipeId: string): void;
  onBack(): void;
  onUnauthorized(): void;
};

function parseLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

export function CreateRecipeScreen({
  catalog,
  onCreated,
  onBack,
  onUnauthorized,
}: CreateRecipeScreenProps) {
  const [title, setTitle] = useState("");
  const [ingredientsText, setIngredientsText] = useState("");
  const [instructionsText, setInstructionsText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      setError("Title is required.");
      return;
    }

    const ingredients = parseLines(ingredientsText).map((rawText) => ({
      rawText,
      name: rawText,
    }));
    const instructions = parseLines(instructionsText);

    setSubmitting(true);
    setError(null);
    try {
      const recipe = await catalog.createRecipe(
        { title: trimmedTitle, ingredients, instructions },
        randomUuid(),
      );
      onCreated(recipe.id);
    } catch (err) {
      if (err instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to create recipe");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ScrollView style={sharedStyles.screen} contentContainerStyle={{ paddingBottom: 40 }}>
      <Pressable accessibilityRole="button" onPress={onBack} style={sharedStyles.buttonSecondary}>
        <Text style={sharedStyles.buttonText}>Back to list</Text>
      </Pressable>

      <Text style={[sharedStyles.heading, { marginTop: 16 }]}>Create recipe</Text>

      <Text style={sharedStyles.label}>Title</Text>
      <TextInput
        value={title}
        onChangeText={setTitle}
        placeholder="Recipe title"
        placeholderTextColor={colors.note}
        style={sharedStyles.input}
      />

      <Text style={sharedStyles.label}>Ingredients (one per line)</Text>
      <TextInput
        value={ingredientsText}
        onChangeText={setIngredientsText}
        placeholder={"2 cups flour\n1 tsp salt"}
        placeholderTextColor={colors.note}
        multiline
        numberOfLines={6}
        style={[sharedStyles.input, { minHeight: 120, textAlignVertical: "top" }]}
      />

      <Text style={sharedStyles.label}>Instructions (one step per line)</Text>
      <TextInput
        value={instructionsText}
        onChangeText={setInstructionsText}
        placeholder={"Mix dry ingredients.\nBake until golden."}
        placeholderTextColor={colors.note}
        multiline
        numberOfLines={8}
        style={[sharedStyles.input, { minHeight: 140, textAlignVertical: "top" }]}
      />

      {error ? <Text style={sharedStyles.error}>{error}</Text> : null}

      <Pressable
        accessibilityRole="button"
        disabled={submitting}
        onPress={() => void submit()}
        style={sharedStyles.button}
      >
        {submitting ? (
          <ActivityIndicator color={colors.badge} />
        ) : (
          <Text style={sharedStyles.buttonText}>Save recipe</Text>
        )}
      </Pressable>
    </ScrollView>
  );
}
