import { Ionicons } from "@expo/vector-icons";
import { usePathname, useRouter } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { mobilePrimaryItems } from "../navigation/registry";
import type { NavigationLink } from "../navigation/types";
import { useTheme } from "../theme/ThemeProvider";

function isActive(item: NavigationLink, pathname: string): boolean {
  return item.routeMatch === "exact" ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

export function BottomNavigation() {
  const { theme } = useTheme();
  const insets = useSafeAreaInsets();
  const pathname = usePathname();
  const router = useRouter();
  return <View testID="bottom-navigation" accessibilityRole="tablist" style={[styles.nav, { backgroundColor: theme.colors.elevatedSurface, borderColor: theme.colors.border, paddingBottom: insets.bottom }]}>{mobilePrimaryItems().map((item) => {
    const active = isActive(item, pathname);
    return <Pressable key={item.id} accessibilityRole="link" accessibilityLabel={item.label} accessibilityHint={active ? "Current page" : "Navigate to page"} accessibilityState={{ selected: active }} onPress={() => router.push(item.href)} style={styles.item}>
      <Ionicons name={item.icon} size={theme.sizing.icon} color={active ? theme.colors.accent : theme.colors.mutedText} />
      <Text style={{ color: active ? theme.colors.accent : theme.colors.mutedText, fontSize: theme.type.caption }}>{item.label}</Text>
    </Pressable>;
  })}</View>;
}

const styles = StyleSheet.create({ nav: { minHeight: 44, borderTopWidth: 1, flexDirection: "row", justifyContent: "space-around" }, item: { minWidth: 44, minHeight: 44, flex: 1, alignItems: "center", justifyContent: "center" } });
