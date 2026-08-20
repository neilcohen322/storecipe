import { Ionicons } from "@expo/vector-icons";
import { Pressable, StyleSheet, View } from "react-native";

import { actionItems } from "../navigation/registry";
import type { NavigationIcon } from "../navigation/types";
import { useTheme } from "../theme/ThemeProvider";
import type { ThemePreference } from "../theme/types";

const themeAction = actionItems.find((item) => item.actionId === "theme");
const choiceIcons: Record<ThemePreference, NavigationIcon> = {
  system: "contrast-outline",
  light: "sunny-outline",
  dark: "moon-outline",
};

export function ThemeControl() {
  const { preference, setPreference, theme } = useTheme();
  if (!themeAction) return null;

  const choices: readonly ThemePreference[] = ["system", "light", "dark"];
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={themeAction.label} style={styles.row}>
      {choices.map((choice) => {
        const selected = choice === preference;
        return <Pressable key={choice} accessibilityRole="button" accessibilityLabel={`Use ${choice} theme`} accessibilityState={{ selected }} onPress={() => void setPreference(choice)} style={[styles.choice, { borderColor: theme.colors.border, backgroundColor: selected ? theme.colors.accent : theme.colors.surface }]}>
          <Ionicons name={choiceIcons[choice]} size={theme.sizing.icon} color={selected ? theme.colors.accentContrast : theme.colors.text} />
        </Pressable>;
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", flexWrap: "wrap", gap: 4 },
  choice: { minWidth: 44, minHeight: 44, borderWidth: 1, borderRadius: 8, alignItems: "center", justifyContent: "center" },
});
