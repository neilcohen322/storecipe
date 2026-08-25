import { useEffect, useRef } from "react";
import { Animated, StyleSheet, Text } from "react-native";

import { useReducedMotion } from "../motion/useReducedMotion";
import { useTheme } from "../theme/ThemeProvider";

export function RecipeReadyReveal({ visible }: { visible: boolean }) {
  const { theme } = useTheme();
  const reducedMotion = useReducedMotion();
  const opacity = useRef(new Animated.Value(1)).current;
  const translateY = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (!visible) return;
    if (reducedMotion) {
      opacity.setValue(1);
      translateY.setValue(0);
      return;
    }
    opacity.setValue(0);
    translateY.setValue(8);
    Animated.parallel([
      Animated.timing(opacity, { toValue: 1, duration: theme.motion.slow, useNativeDriver: true }),
      Animated.timing(translateY, { toValue: 0, duration: theme.motion.slow, useNativeDriver: true }),
    ]).start();
  }, [opacity, reducedMotion, theme.motion.slow, translateY, visible]);

  if (!visible) return null;

  return (
    <Animated.View
      testID="recipe-ready-reveal"
      accessibilityRole="alert"
      accessibilityLiveRegion="polite"
      style={[
        styles.wrap,
        {
          opacity,
          transform: [{ translateY }],
          backgroundColor: theme.colors.elevatedSurface,
          borderColor: theme.colors.success,
        },
      ]}
    >
      <Text accessibilityRole="header" style={[styles.title, { color: theme.colors.text, fontFamily: theme.type.fontFamily.heading }]}>
        Recipe ready
      </Text>
      <Text style={{ color: theme.colors.mutedText }}>Your recipe import is complete.</Text>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderWidth: 1, borderRadius: 16, padding: 16, gap: 8, marginBottom: 16 },
  title: { fontSize: 22, fontWeight: "700" },
});
