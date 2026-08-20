import { Ionicons } from "@expo/vector-icons";
import { usePathname, useRouter } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { desktopNavigationItems } from "../navigation/registry";
import type { NavigationLink } from "../navigation/types";
import { useTheme } from "../theme/ThemeProvider";
import { ThemeControl } from "./ThemeControl";

export const SIDEBAR_EXPANDED_WIDTH = 176;

function isActive(item: NavigationLink, pathname: string): boolean {
  return item.routeMatch === "exact" ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { theme } = useTheme();
  const pathname = usePathname();
  const router = useRouter();
  const items = desktopNavigationItems();
  return <View testID="desktop-sidebar" style={[styles.sidebar, { width: collapsed ? theme.sizing.touchTarget + theme.spacing.sm : SIDEBAR_EXPANDED_WIDTH, backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
    <Pressable accessibilityRole="button" accessibilityLabel={collapsed ? "Expand workspace navigation" : "Collapse workspace navigation"} onPress={onToggle} style={styles.toggle}>
      <Text style={{ color: theme.colors.text, fontSize: theme.type.subtitle }}>{collapsed ? ">" : "<"}</Text>
    </Pressable>
    <View style={styles.group}>{items.map((item) => {
      const active = isActive(item, pathname);
      return <Pressable key={item.id} accessibilityRole="link" accessibilityLabel={item.label} accessibilityHint={active ? "Current page" : "Navigate to page"} accessibilityState={{ selected: active }} onPress={() => router.push(item.href)} style={[styles.link, active && { backgroundColor: theme.colors.elevatedSurface }]}>
        <Ionicons name={item.icon} size={theme.sizing.icon} color={active ? theme.colors.brand : theme.colors.text} />
        {!collapsed && <Text style={{ color: theme.colors.text }}>{item.label}</Text>}
      </Pressable>;
    })}</View>
    {!collapsed && <View style={styles.theme}><ThemeControl /></View>}
  </View>;
}

const styles = StyleSheet.create({
  sidebar: { flexGrow: 0, flexShrink: 0, alignSelf: "stretch", borderRightWidth: 1, padding: 8, gap: 8 },
  toggle: { minWidth: 44, minHeight: 44, alignItems: "center", justifyContent: "center" },
  group: { gap: 4 },
  link: { minWidth: 44, minHeight: 44, borderRadius: 8, paddingHorizontal: 10, flexDirection: "row", alignItems: "center", gap: 8 },
  theme: { marginTop: "auto" },
});
