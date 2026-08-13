import { ActivityIndicator, StyleSheet, Text, TextInput, View } from "react-native";

import { useTheme } from "../theme/ThemeProvider";
import { Button } from "./index";

export type FacetPickerSelection = { name: string; unavailable?: boolean };

export type FacetPickerProps = {
  label: string;
  hint?: string;
  selected: FacetPickerSelection[];
  options: string[];
  search: string;
  onSearch(value: string): void;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore(): void;
  loading?: boolean;
  onAdd(name: string): void;
  onRemove(name: string): void;
};

export function FacetPicker({
  label,
  hint,
  selected,
  options,
  search,
  onSearch,
  hasMore,
  loadingMore,
  onLoadMore,
  loading = false,
  onAdd,
  onRemove,
}: FacetPickerProps) {
  const { theme } = useTheme();
  const selectedNames = new Set(selected.map((item) => item.name));
  const availableOptions = options.filter((option) => !selectedNames.has(option));

  return (
    <View style={styles.container}>
      <Text style={[styles.label, { color: theme.colors.text, fontSize: theme.type.body }]}>{label}</Text>
      {hint ? <Text style={[styles.hint, { color: theme.colors.mutedText, fontSize: theme.type.caption }]}>{hint}</Text> : null}
      {selected.length > 0 ? (
        <View style={styles.chipRow}>
          {selected.map((item) => (
            <View
              key={item.name}
              style={[styles.chip, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}
            >
              <Text style={{ color: theme.colors.text }}>{item.name}</Text>
              {item.unavailable ? <Text style={{ color: theme.colors.mutedText }}>unavailable</Text> : null}
              <Button label={`Remove ${item.name}`} variant="quiet" onPress={() => onRemove(item.name)} />
            </View>
          ))}
        </View>
      ) : null}
      <TextInput
        value={search}
        onChangeText={onSearch}
        accessibilityLabel={label}
        editable={!loading}
        style={[
          styles.search,
          {
            backgroundColor: theme.colors.surface,
            borderColor: theme.colors.border,
            color: theme.colors.text,
            fontSize: theme.type.body,
            minHeight: theme.sizing.control,
          },
        ]}
      />
      {loading ? <ActivityIndicator color={theme.colors.accent} accessibilityLabel={`Loading ${label}`} /> : null}
      <View style={styles.chipRow}>
        {availableOptions.map((option) => (
          <Button key={option} label={option} variant="secondary" onPress={() => onAdd(option)} />
        ))}
      </View>
      {hasMore ? (
        <Button label="Load more options" loading={loadingMore} disabled={loadingMore} onPress={onLoadMore} />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: 8 },
  label: { fontWeight: "600" },
  hint: {},
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { flexDirection: "row", alignItems: "center", gap: 8, minHeight: 44, paddingHorizontal: 12, borderWidth: 1, borderRadius: 999 },
  search: { minWidth: 44, borderWidth: 1, borderRadius: 8, paddingHorizontal: 12 },
});
