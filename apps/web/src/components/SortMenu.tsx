import React, { useEffect, useRef, type RefObject } from "react";
import { Modal, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import type { RecipeSort } from "../api/catalog";
import { useTheme } from "../theme/ThemeProvider";
import { Button } from "./index";

type Focusable = { focus: () => void };

export type SortMenuProps = {
  visible: boolean;
  value: RecipeSort;
  options: { value: RecipeSort; label: string }[];
  onSelect(value: RecipeSort): void;
  onDismiss(): void;
  initialFocusRef?: RefObject<Focusable | null>;
  returnFocusRef?: RefObject<Focusable | null>;
};

export function SortMenu({
  visible,
  value,
  options,
  onSelect,
  onDismiss,
  initialFocusRef,
  returnFocusRef,
}: SortMenuProps) {
  const { theme } = useTheme();
  const selectedRef = useRef<React.ElementRef<typeof Pressable>>(null);
  const titleId = "sort-menu-title";

  useEffect(() => {
    if (visible) (initialFocusRef?.current ?? selectedRef.current)?.focus();
    else returnFocusRef?.current?.focus();
  }, [initialFocusRef, returnFocusRef, visible]);

  useEffect(() => {
    if (!visible || Platform.OS !== "web" || typeof document === "undefined") return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onDismiss();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onDismiss, visible]);

  return (
    <Modal testID="sort-menu" visible={visible} transparent accessibilityViewIsModal onRequestClose={onDismiss}>
      <View style={[styles.scrim, { backgroundColor: theme.colors.scrim }]}>
        <Pressable
          testID="sort-menu-backdrop"
          accessibilityLabel="Dismiss sort"
          onPress={onDismiss}
          style={StyleSheet.absoluteFill}
        />
        <View
          testID="sort-menu-panel"
          role="dialog"
          accessibilityViewIsModal
          aria-modal
          aria-labelledby={titleId}
          style={[styles.panel, { backgroundColor: theme.colors.elevatedSurface }]}
        >
          <Text nativeID={titleId} accessibilityRole="header" style={[styles.title, { color: theme.colors.text }]}>
            Sort
          </Text>
          <View style={styles.options}>
            {options.map((option) => (
              <Button
                key={option.value}
                ref={option.value === value ? selectedRef : undefined}
                label={option.label}
                variant={option.value === value ? "primary" : "secondary"}
                accessibilityState={{ selected: option.value === value }}
                onPress={() => {
                  onSelect(option.value);
                  onDismiss();
                }}
              />
            ))}
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  panel: { width: "100%", maxWidth: 420, gap: 16, padding: 24, borderRadius: 16, zIndex: 1 },
  title: { fontSize: 18, fontWeight: "700" },
  options: { gap: 8 },
});
