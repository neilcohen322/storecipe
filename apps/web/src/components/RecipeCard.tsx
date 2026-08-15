import { Pressable, StyleSheet, Text, View } from "react-native";

import type { Recipe } from "../api/catalog";
import { useTheme } from "../theme/ThemeProvider";
import { RecipeMedia } from "./RecipeMedia";

export type RecipeCardProps = {
  item: Recipe;
  onOpen(recipeId: string): void;
  view: "card" | "list";
};

function mediaColor(recipeId: string, colors: { accent: string; success: string; warning: string; danger: string }): string {
  const palette = [colors.accent, colors.success, colors.warning, colors.danger];
  return palette[recipeId.split("").reduce((sum, char) => sum + char.charCodeAt(0), 0) % palette.length];
}

export function RecipeCard({ item: recipe, onOpen, view }: RecipeCardProps) {
  const { theme } = useTheme();
  const details = [
    recipe.totalMinutes != null ? `${recipe.totalMinutes} min` : null,
    recipe.rating != null ? `${recipe.rating}/5` : "Unrated",
  ].filter((value): value is string => value !== null);

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Open ${recipe.title}`}
      onPress={() => onOpen(recipe.id)}
      focusable
      style={({ pressed }) => [
        styles.container,
        view === "list" && styles.list,
        { backgroundColor: theme.colors.surface, borderColor: theme.colors.border, opacity: pressed ? 0.78 : 1 },
      ]}
    >
      <View testID={`recipe-card-media-${recipe.id}`} style={[styles.mediaSlot, view === "list" && styles.listMedia, { backgroundColor: mediaColor(recipe.id, theme.colors) }]}>
        <RecipeMedia title={recipe.title} tags={recipe.tags} />
      </View>
      <View style={styles.copy}>
        <Text style={[styles.title, { color: theme.colors.text }]}>{recipe.title}</Text>
        <Text style={[styles.details, { color: theme.colors.mutedText }]}>{details.join(" · ")}</Text>
        {recipe.tags.length > 0 ? <Text numberOfLines={1} style={[styles.tags, { color: theme.colors.mutedText }]}>{recipe.tags.join(" · ")}</Text> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { minHeight: 44, width: "100%", maxWidth: "100%", borderWidth: 1, borderRadius: 16, overflow: "hidden" },
  list: { flexDirection: "row" },
  mediaSlot: { minHeight: 140, width: "100%" },
  listMedia: { width: 112, minHeight: 112, minWidth: 112 },
  copy: { padding: 16, gap: 4, flex: 1, minWidth: 0 },
  title: { fontSize: 18, fontWeight: "700" },
  details: { fontSize: 14 },
  tags: { fontSize: 12 },
});
