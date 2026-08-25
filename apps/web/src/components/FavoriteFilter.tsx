import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../theme/ThemeProvider";
import { Button } from "./index";

export type FavoriteFilterProps = {
  value: true | undefined;
  onChange(value: true | undefined): void;
};

export function FavoriteFilter({ value, onChange }: FavoriteFilterProps) {
  const { theme } = useTheme();
  return (
    <View style={styles.container}>
      <Text style={[styles.label, { color: theme.colors.text, fontSize: theme.type.body }]}>Favorites</Text>
      <View style={styles.chipRow}>
        <Button
          label="All recipes"
          variant={value ? "secondary" : "primary"}
          accessibilityState={{ selected: !value }}
          onPress={() => onChange(undefined)}
        />
        <Button
          label="Favorites only"
          variant={value ? "primary" : "secondary"}
          accessibilityState={{ selected: value === true }}
          onPress={() => onChange(true)}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: 8 },
  label: { fontWeight: "600" },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
});
