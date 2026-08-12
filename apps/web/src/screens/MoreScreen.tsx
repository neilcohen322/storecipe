import { Ionicons } from "@expo/vector-icons";
import type { Href } from "expo-router";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { ThemeControl } from "../components/ThemeControl";
import { moreItems } from "../navigation/registry";
import { useTheme } from "../theme/ThemeProvider";

function unsupportedAction(actionId: never): never {
  throw new Error(`Unsupported More action: ${String(actionId)}`);
}

export function MoreScreen({ onNavigate, onLogout }: { onNavigate(href: Href): void; onLogout(): Promise<void> | void }) {
  const { theme } = useTheme();
  return (
    <ScrollView
      testID="more-scroll-view"
      style={{ backgroundColor: theme.colors.canvas }}
      contentContainerStyle={styles.screen}
    >
      <Text accessibilityRole="header" style={[styles.title, { color: theme.colors.text }]}>More</Text>
      <View style={styles.items}>
        {moreItems("compact").map((item) => {
          if (item.kind === "link") {
            return (
              <Pressable key={item.id} accessibilityRole="link" accessibilityLabel={item.label} onPress={() => onNavigate(item.href)} style={[styles.item, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
                <Ionicons name={item.icon} size={theme.sizing.icon} color={theme.colors.text} />
                <Text style={{ color: theme.colors.text }}>{item.label}</Text>
              </Pressable>
            );
          }
          const actionId = item.actionId;
          switch (actionId) {
            case "theme":
              return <View key={item.id} style={[styles.action, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}><Text style={[styles.actionLabel, { color: theme.colors.mutedText }]}>{item.label}</Text><ThemeControl /></View>;
            case "logout":
              return (
                <Pressable key={item.id} accessibilityRole="button" accessibilityLabel={item.label} onPress={() => void onLogout()} style={[styles.item, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
                  <Ionicons name={item.icon} size={theme.sizing.icon} color={theme.colors.danger} />
                  <Text style={{ color: theme.colors.danger }}>{item.label}</Text>
                </Pressable>
              );
            default:
              return unsupportedAction(actionId);
          }
        })}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flexGrow: 1, padding: 24, paddingBottom: 40, gap: 16 },
  title: { fontSize: 28, fontWeight: "800" },
  items: { gap: 12 },
  item: { minHeight: 48, borderWidth: 1, borderRadius: 12, paddingHorizontal: 16, flexDirection: "row", alignItems: "center", gap: 12 },
  action: { borderWidth: 1, borderRadius: 12, padding: 16, gap: 12 },
  actionLabel: { fontSize: 12, fontWeight: "700", letterSpacing: 1 },
});
