import { Ionicons } from "@expo/vector-icons";
import type { Href } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { ThemeControl } from "../components/ThemeControl";
import { moreItems } from "../navigation/registry";
import { useTheme } from "../theme/ThemeProvider";

export function MoreScreen({ onNavigate, onLogout }: { onNavigate(href: Href): void; onLogout(): Promise<void> | void }) {
  const { theme } = useTheme();
  return (
    <View style={[styles.screen, { backgroundColor: theme.colors.canvas }]}>
      <Text accessibilityRole="header" style={[styles.title, { color: theme.colors.text }]}>More</Text>
      <View style={styles.items}>
        {moreItems().map((item) => {
          if (item.kind === "link") {
            return (
              <Pressable key={item.id} accessibilityRole="link" accessibilityLabel={item.label} onPress={() => onNavigate(item.href)} style={[styles.item, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
                <Ionicons name={item.icon} size={theme.sizing.icon} color={theme.colors.text} />
                <Text style={{ color: theme.colors.text }}>{item.label}</Text>
              </Pressable>
            );
          }
          if (item.actionId === "theme") {
            return <View key={item.id} style={[styles.action, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}><Text style={[styles.actionLabel, { color: theme.colors.mutedText }]}>{item.label}</Text><ThemeControl /></View>;
          }
          return (
            <Pressable key={item.id} accessibilityRole="button" accessibilityLabel={item.label} onPress={() => void onLogout()} style={[styles.item, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
              <Ionicons name={item.icon} size={theme.sizing.icon} color={theme.colors.danger} />
              <Text style={{ color: theme.colors.danger }}>{item.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, padding: 24, gap: 16 },
  title: { fontSize: 28, fontWeight: "800" },
  items: { gap: 12 },
  item: { minHeight: 48, borderWidth: 1, borderRadius: 12, paddingHorizontal: 16, flexDirection: "row", alignItems: "center", gap: 12 },
  action: { borderWidth: 1, borderRadius: 12, padding: 16, gap: 12 },
  actionLabel: { fontSize: 12, fontWeight: "700", letterSpacing: 1 },
});
