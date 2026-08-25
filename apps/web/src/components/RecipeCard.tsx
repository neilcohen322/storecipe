import { Pressable, StyleSheet, Text, View } from "react-native";
import { useState } from "react";

import type { Recipe } from "../api/catalog";
import { useTheme } from "../theme/ThemeProvider";
import type { CoverImageLoader } from "./AuthenticatedRecipeImage";
import { RecipeMedia } from "./RecipeMedia";

export type RecipeCardProps = {
  item: Recipe;
  onOpen(recipeId: string): void;
  view: "card" | "list";
  loadCoverImage?: CoverImageLoader;
  onToggleFavorite?(recipe: Recipe): void;
};

function mediaColor(recipeId: string, colors: { brand: string; success: string; warning: string; danger: string }): string {
  const palette = [colors.brand, colors.success, colors.warning, colors.danger];
  return palette[recipeId.split("").reduce((sum, char) => sum + char.charCodeAt(0), 0) % palette.length];
}

function detailsLabel(recipe: Recipe): string {
  return [
    recipe.totalMinutes != null ? `${recipe.totalMinutes} min` : null,
    recipe.servings != null ? `Serves ${recipe.servings}` : null,
    recipe.rating != null ? `${recipe.rating}/5` : "Unrated",
  ].filter((value): value is string => value !== null).join(" · ");
}

export function RecipeCard({ item: recipe, onOpen, view, loadCoverImage, onToggleFavorite }: RecipeCardProps) {
  const { theme } = useTheme();
  const [hovered, setHovered] = useState(false);
  const favoriteLabel = recipe.favorite ? `Remove ${recipe.title} from favorites` : `Add ${recipe.title} to favorites`;
  const tags = recipe.tags.slice(0, 2);
  const meta = detailsLabel(recipe);

  return (
    <View style={styles.wrap}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Open ${recipe.title}`}
        onPress={() => onOpen(recipe.id)}
        onHoverIn={() => setHovered(true)}
        onHoverOut={() => setHovered(false)}
        focusable
        style={({ pressed }) => [
          styles.container,
          view === "list" && styles.list,
          {
            backgroundColor: theme.colors.surface,
            borderColor: theme.colors.border,
            opacity: pressed ? 0.78 : 1,
            ...(hovered && !pressed ? theme.shadows.raised : theme.shadows.none),
          },
        ]}
      >
        <View
          testID={`recipe-card-media-${recipe.id}`}
          style={[
            styles.mediaSlot,
            view === "list" && styles.listMedia,
            { backgroundColor: mediaColor(recipe.id, theme.colors) },
          ]}
        >
          <RecipeMedia
            recipeId={recipe.id}
            title={recipe.title}
            tags={recipe.tags}
            coverImage={recipe.coverImage}
            loadCoverImage={loadCoverImage}
          />
        </View>
        <View style={[styles.copy, view === "list" && styles.listCopy]}>
          <Text style={[styles.title, { color: theme.colors.text, fontFamily: theme.type.fontFamily.heading }]}>{recipe.title}</Text>
          <Text style={[styles.details, { color: theme.colors.mutedText }]}>{meta}</Text>
          {tags.length > 0 ? <Text numberOfLines={1} style={[styles.tags, { color: theme.colors.mutedText }]}>{tags.join(" · ")}</Text> : null}
        </View>
      </Pressable>
      {onToggleFavorite ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={favoriteLabel}
          accessibilityState={{ selected: recipe.favorite }}
          onPress={(event) => {
            event.stopPropagation?.();
            onToggleFavorite(recipe);
          }}
          focusable
          style={({ pressed }) => [
            styles.favorite,
            { backgroundColor: theme.colors.elevatedSurface, opacity: pressed ? 0.78 : 1 },
          ]}
        >
          <Text style={[styles.favoriteMark, { color: recipe.favorite ? theme.colors.warning : theme.colors.mutedText }]}>{recipe.favorite ? "★" : "☆"}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { width: "100%", maxWidth: "100%", position: "relative" },
  container: { minHeight: 44, width: "100%", maxWidth: "100%", borderRadius: 16, overflow: "hidden", borderWidth: 1 },
  list: { flexDirection: "row" },
  mediaSlot: { width: "100%", aspectRatio: 4 / 3 },
  listMedia: { width: 112, minHeight: 84, minWidth: 112, aspectRatio: 4 / 3 },
  copy: { padding: 16, gap: 4 },
  listCopy: { flex: 1, minWidth: 0, justifyContent: "center" },
  title: { fontSize: 18, fontWeight: "700" },
  details: { fontSize: 14 },
  tags: { fontSize: 12 },
  favorite: { position: "absolute", top: 8, right: 8, zIndex: 2, minHeight: 44, minWidth: 44, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  favoriteMark: { fontSize: 22, lineHeight: 26 },
});
