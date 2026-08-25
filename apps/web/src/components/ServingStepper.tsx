import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../theme/ThemeProvider";
import type { ServingStepperValue } from "../utils/ingredientScale";
import { Button } from "./index";

export type ServingStepperProps = {
  servings: number | null;
  value: ServingStepperValue;
  onChange(value: ServingStepperValue): void;
};

const MULTIPLIERS = [0.5, 1, 2] as const;

function multiplierLabel(multiplier: (typeof MULTIPLIERS)[number]): string {
  if (multiplier === 0.5) return "½×";
  if (multiplier === 1) return "1×";
  return "2×";
}

export function ServingStepper({ servings, value, onChange }: ServingStepperProps) {
  const { theme } = useTheme();
  const usesServings = servings != null && servings > 0;

  if (usesServings && value.kind === "servings") {
    const current = value.servings;
    return (
      <View style={styles.container}>
        <Text style={[styles.label, { color: theme.colors.text, fontSize: theme.type.body }]}>Servings</Text>
        <View style={styles.row}>
          <Button
            label="Decrease servings"
            variant="secondary"
            disabled={current <= 1}
            accessibilityState={{ disabled: current <= 1 }}
            onPress={() => onChange({ kind: "servings", servings: Math.max(1, current - 1) })}
          />
          <Text accessibilityRole="text" style={[styles.value, { color: theme.colors.text }]}>{current}</Text>
          <Button
            label="Increase servings"
            variant="secondary"
            disabled={current >= 99}
            accessibilityState={{ disabled: current >= 99 }}
            onPress={() => onChange({ kind: "servings", servings: Math.min(99, current + 1) })}
          />
        </View>
      </View>
    );
  }

  const selectedMultiplier = value.kind === "multiplier" ? value.multiplier : 1;

  return (
    <View style={styles.container}>
      <Text style={[styles.label, { color: theme.colors.text, fontSize: theme.type.body }]}>Scale</Text>
      <View style={styles.row}>
        {MULTIPLIERS.map((multiplier) => (
          <Button
            key={multiplier}
            label={multiplierLabel(multiplier)}
            variant={selectedMultiplier === multiplier ? "primary" : "secondary"}
            accessibilityState={{ selected: selectedMultiplier === multiplier }}
            onPress={() => onChange({ kind: "multiplier", multiplier })}
          />
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: 8 },
  label: { fontWeight: "600" },
  row: { flexDirection: "row", flexWrap: "wrap", gap: 8, alignItems: "center" },
  value: { minWidth: 44, minHeight: 44, textAlign: "center", fontSize: 18, fontWeight: "700", lineHeight: 44 },
});
