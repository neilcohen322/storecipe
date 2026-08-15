import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../theme/ThemeProvider";
import { Button } from "./index";

export type RatingFilterProps = {
  minRating: number | null;
  ratingState: "any" | "rated" | "unrated";
  onMinRating(value: number | null): void;
  onRatingState(value: "any" | "rated" | "unrated"): void;
};

const ratingStates = [
  { value: "any" as const, label: "Any rating" },
  { value: "rated" as const, label: "Rated only" },
  { value: "unrated" as const, label: "Unrated only" },
];

export function RatingFilter({ minRating, ratingState, onMinRating, onRatingState }: RatingFilterProps) {
  const { theme } = useTheme();
  const minDisabled = ratingState === "unrated";

  return (
    <View style={styles.container}>
      <Text style={[styles.label, { color: theme.colors.text, fontSize: theme.type.body }]}>Minimum rating</Text>
      <View style={styles.chipRow}>
        <Button
          label="No minimum"
          variant={minRating === null ? "primary" : "secondary"}
          accessibilityState={{ selected: minRating === null, disabled: minDisabled }}
          disabled={minDisabled}
          onPress={() => onMinRating(null)}
        />
        {[1, 2, 3, 4, 5].map((rating) => (
          <Button
            key={rating}
            label={`${rating} and up`}
            variant={minRating === rating ? "primary" : "secondary"}
            accessibilityState={{ selected: minRating === rating, disabled: minDisabled }}
            disabled={minDisabled}
            onPress={() => onMinRating(rating)}
          />
        ))}
      </View>
      <Text style={[styles.label, { color: theme.colors.text, fontSize: theme.type.body }]}>Rating state</Text>
      <View style={styles.chipRow}>
        {ratingStates.map((state) => (
          <Button
            key={state.value}
            label={state.label}
            variant={ratingState === state.value ? "primary" : "secondary"}
            accessibilityState={{ selected: ratingState === state.value }}
            onPress={() => onRatingState(state.value)}
          />
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: 8 },
  label: { fontWeight: "600" },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
});
