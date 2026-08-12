import type { ReactNode } from "react";
import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../theme/ThemeProvider";

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  const { theme } = useTheme();
  return (
    <View accessibilityLabel="Empty state" style={styles.state}>
      <Text accessibilityRole="header" style={[styles.title, { color: theme.colors.text }]}>{title}</Text>
      {description ? <Text style={[styles.description, { color: theme.colors.mutedText }]}>{description}</Text> : null}
      {action}
    </View>
  );
}

const styles = StyleSheet.create({
  state: { minHeight: 44, padding: 16, alignItems: "center", justifyContent: "center", gap: 8 },
  title: { fontSize: 18, fontWeight: "700" },
  description: { fontSize: 12 },
});
