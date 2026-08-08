import { Pressable, Text, View } from "react-native";

import { sharedStyles } from "../theme";

export type LandingScreenProps = {
  authConfigured: boolean;
  isLoading: boolean;
  isAuthenticated: boolean;
  errorMessage?: string | null;
  onLogin(): void;
  onContinue(): void;
};

export function LandingScreen({
  authConfigured,
  isLoading,
  isAuthenticated,
  errorMessage,
  onLogin,
  onContinue,
}: LandingScreenProps) {
  return (
    <View style={sharedStyles.centered}>
      <View style={sharedStyles.badge}>
        <Text style={sharedStyles.badgeText}>PRIVATE RECIPE LIBRARY</Text>
      </View>
      <Text style={sharedStyles.title}>Storecipe</Text>
      <Text style={sharedStyles.subtitle}>
        Your private recipe library, ready for thoughtful recommendations.
      </Text>

      {!authConfigured ? (
        <Text style={sharedStyles.error}>
          Set EXPO_PUBLIC_AUTH0_DOMAIN, EXPO_PUBLIC_AUTH0_CLIENT_ID, and
          EXPO_PUBLIC_AUTH0_AUDIENCE to enable login.
        </Text>
      ) : isLoading ? (
        <Text style={sharedStyles.note}>Checking session…</Text>
      ) : isAuthenticated ? (
        <Pressable
          accessibilityRole="button"
          onPress={onContinue}
          style={sharedStyles.button}
        >
          <Text style={sharedStyles.buttonText}>Continue to recipes</Text>
        </Pressable>
      ) : (
        <Pressable
          accessibilityRole="button"
          onPress={onLogin}
          style={sharedStyles.button}
        >
          <Text style={sharedStyles.buttonText}>Log in</Text>
        </Pressable>
      )}

      {errorMessage ? <Text style={sharedStyles.error}>{errorMessage}</Text> : null}
      <Text style={sharedStyles.note}>The Expo universal client is running.</Text>
    </View>
  );
}
