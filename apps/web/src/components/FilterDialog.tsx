import React, { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { Modal, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { ListRecipesParams } from "../api/catalog";
import type { LayoutMode } from "../navigation/types";
import { useTheme } from "../theme/ThemeProvider";
import { Button } from "./index";

export type FilterDraft = Pick<
  ListRecipesParams,
  "ingredient" | "tag" | "maxTotalMinutes" | "minRating" | "ratingState"
>;

type Focusable = { focus: () => void };

export type FilterDialogProps = {
  visible: boolean;
  layoutMode: LayoutMode;
  draft: FilterDraft;
  onChange(next: FilterDraft): void;
  onApply(): void;
  onClear(): void;
  onDismiss(): void;
  children: ReactNode;
  initialFocusRef?: RefObject<Focusable | null>;
  returnFocusRef?: RefObject<Focusable | null>;
};

export function FilterDialog({
  visible,
  layoutMode,
  draft: _draft,
  onChange: _onChange,
  onApply,
  onClear,
  onDismiss,
  children,
  initialFocusRef,
  returnFocusRef,
}: FilterDialogProps) {
  const { theme } = useTheme();
  const insets = useSafeAreaInsets();
  const compact = layoutMode === "compact";
  const titleId = "filter-dialog-title";
  const wasVisible = useRef(visible);

  useEffect(() => {
    if (visible) initialFocusRef?.current?.focus();
    else if (wasVisible.current) returnFocusRef?.current?.focus();
    wasVisible.current = visible;
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
    <Modal
      testID="filter-dialog"
      visible={visible}
      transparent
      accessibilityViewIsModal
      onRequestClose={onDismiss}
    >
      <View pointerEvents="box-none" style={[styles.scrim, compact && styles.compactScrim, { backgroundColor: theme.colors.scrim }]}>
        <Pressable
          testID="filter-dialog-backdrop"
          accessibilityLabel="Dismiss filters"
          onPress={onDismiss}
          style={StyleSheet.absoluteFill}
        />
        <View
          testID="filter-dialog-panel"
          role="dialog"
          accessibilityViewIsModal
          aria-modal
          aria-labelledby={titleId}
          style={[
            styles.panel,
            {
              backgroundColor: theme.colors.elevatedSurface,
              maxWidth: compact ? undefined : 720,
            },
            compact && {
              flex: 1,
              paddingTop: insets.top,
              paddingRight: insets.right,
              paddingBottom: insets.bottom,
              paddingLeft: insets.left,
              borderRadius: 0,
            },
          ]}
        >
          <Text nativeID={titleId} accessibilityRole="header" style={[styles.title, { color: theme.colors.text }]}>
            Filters
          </Text>
          <ScrollView
            style={compact ? styles.compactBody : styles.body}
            contentContainerStyle={styles.bodyContent}
            keyboardShouldPersistTaps="handled"
          >
            {children}
          </ScrollView>
          <View style={styles.actions}>
            <Button testID="filter-dialog-clear" label="Clear" variant="quiet" onPress={onClear} />
            <Button testID="filter-dialog-cancel" label="Cancel" variant="secondary" onPress={onDismiss} />
            <Button testID="filter-dialog-apply" label="Apply" onPress={onApply} />
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  compactScrim: { padding: 0 },
  panel: { width: "100%", gap: 16, padding: 24, borderRadius: 16, zIndex: 1, maxHeight: "100%" },
  title: { fontSize: 18, fontWeight: "700" },
  body: { maxHeight: 420 },
  compactBody: { flex: 1, minHeight: 0 },
  bodyContent: { gap: 16, flexGrow: 1 },
  actions: { flexDirection: "row", justifyContent: "flex-end", flexWrap: "wrap", gap: 8 },
});
