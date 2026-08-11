import { Pressable, StyleSheet, Text, View } from "react-native";

import { ThemeControl } from "../components/ThemeControl";
import { useTheme } from "../theme/ThemeProvider";

export function AccountScreen({ identity, onLogout }: { identity: { name?: string; email?: string; picture?: string } | null; onLogout(): Promise<void> }) {
  const { theme } = useTheme();
  return <View style={[styles.screen, { backgroundColor: theme.colors.canvas }]}>
    <Text style={[styles.title, { color: theme.colors.text }]}>Account</Text>
    <View style={[styles.section, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
      <Text style={[styles.label, { color: theme.colors.mutedText }]}>SIGNED IN AS</Text>
      <Text style={[styles.name, { color: theme.colors.text }]}>{identity?.name ?? "Storecipe member"}</Text>
      {identity?.email ? <Text style={{ color: theme.colors.mutedText }}>{identity.email}</Text> : null}
    </View>
    <View style={[styles.section, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
      <Text style={[styles.label, { color: theme.colors.mutedText }]}>THEME</Text>
      <ThemeControl />
    </View>
    <Pressable accessibilityRole="button" accessibilityLabel="Log out" onPress={() => void onLogout()} style={[styles.logout, { borderColor: theme.colors.danger }]}>
      <Text style={{ color: theme.colors.danger, fontWeight: "700" }}>Log out</Text>
    </Pressable>
  </View>;
}

const styles = StyleSheet.create({ screen: { flex: 1, padding: 24, gap: 16 }, title: { fontSize: 28, fontWeight: "800" }, section: { borderWidth: 1, borderRadius: 12, padding: 16, gap: 6 }, label: { fontSize: 12, fontWeight: "700", letterSpacing: 1 }, name: { fontSize: 18, fontWeight: "700" }, logout: { minHeight: 48, alignItems: "center", justifyContent: "center", borderWidth: 1, borderRadius: 12, marginTop: 8 } });
