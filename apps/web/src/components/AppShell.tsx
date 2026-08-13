import { Ionicons } from "@expo/vector-icons";
import { usePathname, useRouter } from "expo-router";
import { type PropsWithChildren, useState } from "react";
import { Platform, Pressable, StyleSheet, useWindowDimensions, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { linkItems } from "../navigation/registry";
import type { LayoutMode } from "../navigation/types";
import { useTheme } from "../theme/ThemeProvider";
import { getTheme } from "../theme/tokens";
import { BottomNavigation, COMPACT_NAVIGATION_HEIGHT } from "./BottomNavigation";
import { Sidebar } from "./Sidebar";

const SIDEBAR_STORAGE_KEY = "storecipe.sidebar-collapsed";
const layoutBreakpoints = getTheme("light").breakpoints;

function readCollapsedPreference(): boolean {
  if (Platform.OS !== "web" || typeof window === "undefined") return false;
  try { return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true"; } catch { return false; }
}

function persistCollapsedPreference(collapsed: boolean): void {
  if (Platform.OS !== "web" || typeof window === "undefined") return;
  try { window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(collapsed)); } catch { /* preference storage is optional */ }
}

export function getLayoutMode(width: number): LayoutMode {
  if (width < layoutBreakpoints.medium) return "compact";
  if (width < layoutBreakpoints.expanded) return "medium";
  return "expanded";
}

export function AppShell({ children, viewportWidth }: PropsWithChildren<{ viewportWidth?: number }>) {
  const { width } = useWindowDimensions();
  const mode = getLayoutMode(viewportWidth ?? width);
  const { theme } = useTheme();
  const insets = useSafeAreaInsets();
  const pathname = usePathname();
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(readCollapsedPreference);
  const create = linkItems.find((item) => item.id === "create");
  const toggleSidebar = () => setCollapsed((current) => {
    const next = !current;
    persistCollapsedPreference(next);
    return next;
  });
  const content = <View testID="app-shell-content" style={styles.content}>{children}</View>;
  if (mode === "compact") {
    return (
      <View testID="app-shell-compact" style={[styles.compact, { backgroundColor: theme.colors.canvas, paddingBottom: insets.bottom + COMPACT_NAVIGATION_HEIGHT }]}>
        {content}
        <BottomNavigation />
      </View>
    );
  }
  return (
    <View testID={`app-shell-${mode}`} style={[styles.desktop, { backgroundColor: theme.colors.canvas }]}>
      <Sidebar collapsed={collapsed} onToggle={toggleSidebar} />
      <View style={[styles.canvas, { paddingTop: insets.top + theme.spacing.md, paddingRight: insets.right + theme.spacing.lg, paddingBottom: insets.bottom + theme.spacing.lg, paddingLeft: theme.spacing.lg }]}>
        <View style={styles.pageAction}>
          {create && pathname !== create.href && (
            <Pressable
              testID="page-create-action"
              accessibilityRole="link"
              accessibilityLabel={create.label}
              onPress={() => router.push(create.href)}
              style={[styles.createAction, { backgroundColor: theme.colors.accent }]}
            >
              <Ionicons name={create.icon} size={theme.sizing.icon} color={theme.colors.accentContrast} />
            </Pressable>
          )}
        </View>
        {content}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  compact: { flex: 1, minHeight: 0 },
  desktop: { flex: 1, flexDirection: "row", minHeight: 0 },
  canvas: { flex: 1, minHeight: 0, minWidth: 0, alignItems: "center" },
  content: { flex: 1, width: "100%", maxWidth: 1120, minHeight: 0, minWidth: 0 },
  pageAction: { width: "100%", maxWidth: 1120, alignItems: "flex-end", marginBottom: 16 },
  createAction: { minWidth: 44, minHeight: 44, borderRadius: 12, alignItems: "center", justifyContent: "center" },
});
