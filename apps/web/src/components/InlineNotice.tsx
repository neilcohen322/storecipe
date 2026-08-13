import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../theme/ThemeProvider";

export type NoticeTone = "info" | "success" | "warning" | "error";

const toneLabels: Record<NoticeTone, string> = {
  info: "Information",
  success: "Success",
  warning: "Warning",
  error: "Error",
};

export function InlineNotice({ message, tone = "info" }: { message: string; tone?: NoticeTone }) {
  const { theme } = useTheme();
  const toneColor = tone === "error"
    ? theme.colors.danger
    : tone === "warning"
      ? theme.colors.warning
      : tone === "success"
        ? theme.colors.success
        : theme.colors.text;
  return (
    <View
      testID="inline-notice"
      accessibilityRole="alert"
      accessibilityLiveRegion={tone === "error" ? "assertive" : "polite"}
      style={[styles.notice, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface }]}
    >
      <Text style={[styles.label, { color: toneColor }]}>{toneLabels[tone]}</Text>
      <Text style={{ color: theme.colors.text }}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  notice: { minHeight: 44, padding: 12, borderWidth: 1, borderRadius: 8, justifyContent: "center", gap: 4 },
  label: { fontSize: 12, fontWeight: "700" },
});
