import type { ReactNode } from "react";
import { TextInput } from "react-native";

import { Field, TextArea } from "../components";
import { useTheme } from "../theme/ThemeProvider";

export const PERSONAL_NOTES_MAX_LENGTH = 5000;

export type RecipeFormFieldsProps = {
  title: string;
  onTitleChange(value: string): void;
  titleError?: string;
  ingredientsText: string;
  onIngredientsChange(value: string): void;
  ingredientsError?: string;
  instructionsText: string;
  onInstructionsChange(value: string): void;
  instructionsError?: string;
  personalNotes?: string;
  onPersonalNotesChange?(value: string): void;
  notesError?: string;
  afterIngredients?: ReactNode;
  disabled?: boolean;
  onSubmitEditing?(): void;
};

export function RecipeFormFields({
  title,
  onTitleChange,
  titleError,
  ingredientsText,
  onIngredientsChange,
  ingredientsError,
  instructionsText,
  onInstructionsChange,
  instructionsError,
  personalNotes,
  onPersonalNotesChange,
  notesError,
  afterIngredients,
  disabled = false,
  onSubmitEditing,
}: RecipeFormFieldsProps) {
  const { theme } = useTheme();
  return (
    <>
      <Field
        label="Title"
        hint="For example: Weeknight tomato soup"
        error={titleError}
        control={
          <TextInput
            value={title}
            onChangeText={onTitleChange}
            placeholder="Recipe title"
            placeholderTextColor={theme.colors.mutedText}
            editable={!disabled}
            returnKeyType="done"
            onSubmitEditing={onSubmitEditing}
          />
        }
      />
      <TextArea
        label="Ingredients"
        hint="One ingredient per line, for example: 2 cups tomatoes"
        error={ingredientsError}
        value={ingredientsText}
        onChangeText={onIngredientsChange}
        placeholder={"2 cups tomatoes\n1 tsp salt"}
        placeholderTextColor={theme.colors.mutedText}
        editable={!disabled}
        numberOfLines={6}
      />
      {afterIngredients}
      <TextArea
        label="Instructions"
        hint="One step per line, for example: Simmer for 20 minutes."
        error={instructionsError}
        value={instructionsText}
        onChangeText={onInstructionsChange}
        placeholder={"Chop the vegetables.\nSimmer until tender."}
        placeholderTextColor={theme.colors.mutedText}
        editable={!disabled}
        numberOfLines={8}
        returnKeyType="done"
        blurOnSubmit
        onSubmitEditing={onSubmitEditing}
      />
      {onPersonalNotesChange ? (
        <TextArea
          label="Personal notes"
          hint="Private notes, up to 5,000 characters."
          error={notesError}
          value={personalNotes ?? ""}
          onChangeText={onPersonalNotesChange}
          maxLength={PERSONAL_NOTES_MAX_LENGTH}
          placeholder="Salt the pasta water well."
          placeholderTextColor={theme.colors.mutedText}
          editable={!disabled}
          numberOfLines={4}
        />
      ) : null}
    </>
  );
}
