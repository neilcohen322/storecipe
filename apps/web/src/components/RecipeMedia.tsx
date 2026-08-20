import { Text, View } from "react-native";

import type { CoverImage } from "../api/catalog";
import { useTheme } from "../theme/ThemeProvider";
import { AuthenticatedRecipeImage, type CoverImageLoader } from "./AuthenticatedRecipeImage";

export type RecipeMediaProps = {
  title: string;
  tags?: string[];
  recipeId?: string;
  coverImage?: CoverImage | null;
  loadCoverImage?: CoverImageLoader;
};

function Placeholder({ title, tags = [] }: { title: string; tags?: string[] }) {
  const { theme } = useTheme();
  const initials = title.trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase() || "R";
  const seed = `${title}${tags.join("")}`.split("").reduce((sum, char) => sum + char.charCodeAt(0), 0);
  const colors = [theme.colors.brand, theme.colors.success, theme.colors.warning, theme.colors.danger];
  return (
    <View
      testID="recipe-media"
      accessibilityRole="image"
      accessibilityLabel={`${title} recipe placeholder`}
      style={{ minHeight: 160, alignItems: "center", justifyContent: "center", borderRadius: 16, backgroundColor: colors[seed % colors.length], width: "100%", height: "100%" }}
    >
      <Text style={{ fontSize: 36, fontWeight: "700", fontFamily: theme.type.fontFamily.heading, color: theme.colors.accentContrast }}>{initials}</Text>
    </View>
  );
}

export function RecipeMedia({ title, tags = [], recipeId, coverImage, loadCoverImage }: RecipeMediaProps) {
  const placeholder = <Placeholder title={title} tags={tags} />;
  if (!recipeId || !coverImage || !loadCoverImage) {
    return placeholder;
  }
  return (
    <AuthenticatedRecipeImage
      recipeId={recipeId}
      title={title}
      etag={coverImage.etag}
      url={coverImage.url}
      loadCoverImage={loadCoverImage}
      fallback={placeholder}
    />
  );
}
