import { Ionicons } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { actionItems } from "../navigation/registry";
import { useTheme } from "../theme/ThemeProvider";
import type { ThemePreference } from "../theme/types";

const themeAction = actionItems.find((item) => item.actionId === "theme");

export function ThemeControl() {
  const { preference, setPreference, theme } = useTheme();
  if (!themeAction) return null;

  const choices: readonly ThemePreference[] = ["system", "light", "dark"];
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={themeAction.label} style={styles.row}>
      {choices.map((choice) => {
        const selected = choice === preference;
        return <Pressable key={choice} accessibilityRole="button" accessibilityLabel={`Use ${choice} theme`} accessibilityState={{ selected }} onPress={() => void setPreference(choice)} style={[styles.choice, { borderColor: theme.colors.border, backgroundColor: selected ? theme.colors.accent : theme.colors.surface }]}>
          <Ionicons name={themeAction.icon} size={theme.sizing.icon} color={selected ? theme.colors.accentContrast : theme.colors.text} />
          <Text style={{ color: selected ? theme.colors.accentContrast : theme.colors.text }}>{choice}</Text>
        </Pressable>;
      })}
    </View>
  );
}

const styles = StyleSheet.create({ row: { flexDirection: "row", flexWrap: "wrap", gap: 8 }, choice: { minWidth: 44, minHeight: 44, borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 4 } });
