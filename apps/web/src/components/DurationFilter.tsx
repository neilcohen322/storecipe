import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../theme/ThemeProvider";
import { Button } from "./index";

export type DurationFilterProps = {
  observed: { min: number; max: number } | null;
  value: number | null;
  onChange(value: number | null): void;
};

function isInRange(value: number, observed: { min: number; max: number }) {
  return value >= 0 && value <= observed.max;
}

function clamp(value: number, observed: { min: number; max: number }) {
  return Math.max(0, Math.min(observed.max, value));
}

export function DurationFilter({ observed, value, onChange }: DurationFilterProps) {
  const { theme } = useTheme();
  const unavailable = observed === null && value !== null;
  const outOfRange = observed !== null && value !== null && !isInRange(value, observed);
  const anyDurationSelected = value === null && !unavailable;
  const showStepper = observed !== null && value !== null;
  const showMaxShortcut = observed !== null && value === null;

  const adjust = (direction: "decrease" | "increase") => {
    if (!observed || value === null) return;
    const bounded = clamp(value, observed);
    const next = direction === "decrease"
      ? Math.max(0, bounded - 1)
      : Math.min(observed.max, bounded + 1);
    onChange(next);
  };

  return (
    <View style={styles.container}>
      <Text style={[styles.label, { color: theme.colors.text, fontSize: theme.type.body }]}>Maximum duration</Text>
      <View style={styles.chipRow}>
        {observed !== null || unavailable || outOfRange ? (
          <Button
            label="Any duration"
            variant={anyDurationSelected ? "primary" : "secondary"}
            accessibilityState={{ selected: anyDurationSelected }}
            onPress={() => onChange(null)}
          />
        ) : null}
        {showMaxShortcut ? (
          <Button
            label={`${observed.max} minutes`}
            variant="secondary"
            onPress={() => onChange(observed.max)}
          />
        ) : null}
      </View>
      {unavailable ? (
        <View style={[styles.chip, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
          <Text style={{ color: theme.colors.text }}>{value} minutes</Text>
          <Text style={{ color: theme.colors.mutedText }}>unavailable</Text>
          <Button label={`Clear ${value} minutes`} variant="quiet" onPress={() => onChange(null)} />
        </View>
      ) : null}
      {outOfRange ? (
        <View style={[styles.chip, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
          <Text style={{ color: theme.colors.text }}>{value} minutes</Text>
          <Text style={{ color: theme.colors.mutedText }}>outside current range</Text>
        </View>
      ) : null}
      {showStepper ? (
        <View style={styles.stepper}>
          <Button label="Decrease duration" variant="secondary" onPress={() => adjust("decrease")} />
          <Text
            accessibilityRole="text"
            accessibilityLabel={`${value} minutes`}
            style={[styles.value, { color: theme.colors.text, fontSize: theme.type.body }]}
          >
            {value} minutes
          </Text>
          <Button label="Increase duration" variant="secondary" onPress={() => adjust("increase")} />
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: 8 },
  label: { fontWeight: "600" },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { flexDirection: "row", alignItems: "center", gap: 8, minHeight: 44, paddingHorizontal: 12, borderWidth: 1, borderRadius: 999 },
  stepper: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 },
  value: { minHeight: 44, minWidth: 44, textAlignVertical: "center", textAlign: "center", fontWeight: "600" },
});
