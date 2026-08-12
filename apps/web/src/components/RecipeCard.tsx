import type { ComponentProps, ComponentType } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import type { RecipeQueryItem } from "../api/catalog";
import { useTheme } from "../theme/ThemeProvider";
import { RecipeMedia } from "./RecipeMedia";

export type RecipeCardProps = {
  item: RecipeQueryItem;
  onOpen(recipeId: string): void;
  view: "card" | "list";
};

type WebPressableProps = ComponentProps<typeof Pressable> & {
  onKeyDown?: (event: { nativeEvent?: { key?: string }; preventDefault?: () => void }) => void;
};

const WebPressable = Pressable as unknown as ComponentType<WebPressableProps>;

function mediaColor(recipeId: string, colors: { accent: string; success: string; warning: string; danger: string }): string {
  const palette = [colors.accent, colors.success, colors.warning, colors.danger];
  return palette[recipeId.split("").reduce((sum, char) => sum + char.charCodeAt(0), 0) % palette.length];
}

export function RecipeCard({ item, onOpen, view }: RecipeCardProps) {
  const { recipe } = item;
  const { theme } = useTheme();
  const details = [
    recipe.totalMinutes != null ? `${recipe.totalMinutes} min` : null,
    recipe.rating != null ? `${recipe.rating}/5` : "Unrated",
  ].filter((value): value is string => value !== null);
  const webKeyboardProps = Platform.OS === "web" ? {
    onKeyDown: (event: { nativeEvent?: { key?: string }; preventDefault?: () => void }) => {
      if (event.nativeEvent?.key === "Enter" || event.nativeEvent?.key === " ") { event.preventDefault?.(); onOpen(recipe.id); }
    },
  } : {};

  return (
    <WebPressable
      accessibilityRole="button"
      accessibilityLabel={`Open ${recipe.title}`}
      onPress={() => onOpen(recipe.id)}
      focusable
      {...webKeyboardProps}
      style={({ pressed }) => [
        styles.container,
        view === "list" && styles.list,
        { backgroundColor: theme.colors.surface, borderColor: theme.colors.border, opacity: pressed ? 0.78 : 1 },
      ]}
    >
      <View testID={`recipe-card-media-${recipe.id}`} style={[styles.mediaSlot, { backgroundColor: mediaColor(recipe.id, theme.colors) }]}>
        <RecipeMedia title={recipe.title} tags={recipe.tags} />
      </View>
      <View style={styles.copy}>
        <Text style={[styles.title, { color: theme.colors.text }]}>{recipe.title}</Text>
        <Text style={[styles.details, { color: theme.colors.mutedText }]}>{details.join(" · ")}</Text>
        {recipe.tags.length > 0 ? <Text numberOfLines={1} style={[styles.tags, { color: theme.colors.mutedText }]}>{recipe.tags.join(" · ")}</Text> : null}
      </View>
    </WebPressable>
  );
}

const styles = StyleSheet.create({
  container: { minHeight: 44, borderWidth: 1, borderRadius: 16, overflow: "hidden" },
  list: { flexDirection: "row" },
  mediaSlot: { minHeight: 8 },
  copy: { padding: 16, gap: 4, flex: 1 },
  title: { fontSize: 18, fontWeight: "700" },
  details: { fontSize: 14 },
  tags: { fontSize: 12 },
});
