import { Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../theme/ThemeProvider";

export type LandingScreenProps = {
  authPresentation: "auth0" | "demo";
  authConfigured: boolean;
  isLoading: boolean;
  isAuthenticated: boolean;
  errorMessage?: string | null;
  onLogin(): void;
  onContinue(): void;
};

export function LandingScreen({ authPresentation, authConfigured, isLoading, isAuthenticated, errorMessage, onLogin, onContinue }: LandingScreenProps) {
  const { theme } = useTheme();
  const loginLabel = authPresentation === "demo" ? "Explore demo" : "Sign in";
  return (
    <View style={[styles.screen, { backgroundColor: theme.colors.canvas }]}>
      <View style={styles.copy}>
        <Text style={[styles.eyebrow, { color: theme.colors.mutedText }]}>STORECIPE</Text>
        <Text style={[styles.title, { color: theme.colors.text }]}>Your recipes, gathered in one calm place.</Text>
        <Text style={[styles.subtitle, { color: theme.colors.mutedText }]}>Keep the meals you want to make close at hand.</Text>
        <View style={styles.action}>
          {!authConfigured ? <Text style={[styles.error, { color: theme.colors.danger }]}>Set EXPO_PUBLIC_AUTH0_DOMAIN, EXPO_PUBLIC_AUTH0_CLIENT_ID, and EXPO_PUBLIC_AUTH0_AUDIENCE to enable login.</Text>
            : isLoading ? <Text style={[styles.note, { color: theme.colors.mutedText }]}>Checking session…</Text>
              : isAuthenticated ? <Pressable accessibilityRole="button" accessibilityLabel="Continue to recipes" onPress={onContinue} style={[styles.button, { backgroundColor: theme.colors.accent }]}><Text style={[styles.buttonText, { color: theme.colors.accentContrast }]}>Continue to recipes</Text></Pressable>
                : <Pressable accessibilityRole="button" accessibilityLabel={loginLabel} onPress={onLogin} style={[styles.button, { backgroundColor: theme.colors.accent }]}><Text style={[styles.buttonText, { color: theme.colors.accentContrast }]}>{loginLabel}</Text></Pressable>}
        </View>
        {errorMessage ? <Text accessibilityRole="alert" accessibilityLiveRegion="assertive" style={[styles.error, { color: theme.colors.danger }]}>We couldn't sign you in. Please try again.</Text> : null}
        <Text style={[styles.note, { color: theme.colors.mutedText }]}>Authentication is handled securely.</Text>
      </View>
      <View accessibilityLabel="Recipe library preview" style={[styles.preview, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
        <Text style={[styles.previewTitle, { color: theme.colors.text }]}>Recipe library</Text>
        {["Weeknight pasta", "Lemon chicken", "Summer salad"].map((recipe) => <View key={recipe} style={[styles.recipe, { borderColor: theme.colors.border }]}><View style={[styles.thumbnail, { backgroundColor: theme.colors.elevatedSurface }]} /><Text style={{ color: theme.colors.text }}>{recipe}</Text></View>)}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({ screen: { flex: 1, alignItems: "center", justifyContent: "center", gap: 32, padding: 24 }, copy: { width: "100%", maxWidth: 520 }, eyebrow: { fontSize: 12, fontWeight: "700", letterSpacing: 1.2 }, title: { fontSize: 40, fontWeight: "800", letterSpacing: -1, marginTop: 12 }, subtitle: { fontSize: 18, lineHeight: 26, marginTop: 12 }, action: { marginTop: 24, alignItems: "flex-start" }, button: { minHeight: 48, borderRadius: 12, justifyContent: "center", paddingHorizontal: 20 }, buttonText: { fontSize: 15, fontWeight: "700" }, note: { fontSize: 13, lineHeight: 19, marginTop: 12 }, error: { fontSize: 14, lineHeight: 20, marginTop: 12 }, preview: { width: "100%", maxWidth: 520, borderWidth: 1, borderRadius: 16, padding: 16, gap: 12 }, previewTitle: { fontSize: 16, fontWeight: "700" }, recipe: { flexDirection: "row", alignItems: "center", gap: 12, borderTopWidth: 1, paddingTop: 12 }, thumbnail: { width: 40, height: 40, borderRadius: 8 } });
