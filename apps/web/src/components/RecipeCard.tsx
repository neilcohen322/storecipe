import { Pressable, StyleSheet, Text, View } from "react-native";

import type { Recipe } from "../api/catalog";
import { useTheme } from "../theme/ThemeProvider";
import type { CoverImageLoader } from "./AuthenticatedRecipeImage";
import { RecipeMedia } from "./RecipeMedia";

export type RecipeCardProps = {
  item: Recipe;
  onOpen(recipeId: string): void;
  view: "card" | "list";
  loadCoverImage?: CoverImageLoader;
};

function mediaColor(recipeId: string, colors: { brand: string; success: string; warning: string; danger: string }): string {
  const palette = [colors.brand, colors.success, colors.warning, colors.danger];
  return palette[recipeId.split("").reduce((sum, char) => sum + char.charCodeAt(0), 0) % palette.length];
}

export function RecipeCard({ item: recipe, onOpen, view, loadCoverImage }: RecipeCardProps) {
  const { theme } = useTheme();
  const details = [
    recipe.totalMinutes != null ? `${recipe.totalMinutes} min` : null,
    recipe.rating != null ? `${recipe.rating}/5` : "Unrated",
  ].filter((value): value is string => value !== null);
  const detailsLabel = details.join(" · ");

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
        <RecipeMedia
          recipeId={recipe.id}
          title={recipe.title}
          tags={recipe.tags}
          coverImage={recipe.coverImage}
          loadCoverImage={loadCoverImage}
        />
        {view === "card" ? (
          <View style={[styles.overlay, { backgroundColor: theme.colors.overlayScrim }]}>
            <Text style={[styles.overlayTitle, { color: theme.colors.accentContrast, fontFamily: theme.type.fontFamily.heading }]}>{recipe.title}</Text>
            <Text style={[styles.overlayDetails, { color: theme.colors.accentContrast }]}>{detailsLabel}</Text>
          </View>
        ) : null}
      </View>
      {view === "list" ? (
        <View style={styles.copy}>
          <Text style={[styles.title, { color: theme.colors.text }]}>{recipe.title}</Text>
          <Text style={[styles.details, { color: theme.colors.mutedText }]}>{detailsLabel}</Text>
          {recipe.tags.length > 0 ? <Text numberOfLines={1} style={[styles.tags, { color: theme.colors.mutedText }]}>{recipe.tags.join(" · ")}</Text> : null}
        </View>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { minHeight: 44, width: "100%", maxWidth: "100%", borderRadius: 16, overflow: "hidden" },
  list: { flexDirection: "row", borderWidth: 1 },
  mediaSlot: { minHeight: 240, width: "100%" },
  listMedia: { width: 112, minHeight: 112, minWidth: 112 },
  overlay: { position: "absolute", left: 0, right: 0, bottom: 0, padding: 16, gap: 4 },
  overlayTitle: { fontSize: 18, fontWeight: "700" },
  overlayDetails: { fontSize: 14 },
  copy: { padding: 16, gap: 4, flex: 1, minWidth: 0 },
  title: { fontSize: 18, fontWeight: "700" },
  details: { fontSize: 14 },
  tags: { fontSize: 12 },
});
