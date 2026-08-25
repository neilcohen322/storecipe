import { Image, StyleSheet } from "react-native";

const HERB_ART = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 180" fill="none"><path d="M60 170 C58 120 40 90 22 70" stroke="#c4b5a5" stroke-width="1.4" stroke-linecap="round"/><path d="M60 170 C62 118 82 88 102 64" stroke="#c4b5a5" stroke-width="1.4" stroke-linecap="round"/><path d="M60 140 C48 118 36 108 20 102" stroke="#d4c4b4" stroke-width="1.2" stroke-linecap="round"/><ellipse cx="22" cy="68" rx="10" ry="16" transform="rotate(-30 22 68)" stroke="#c4b5a5" stroke-width="1.2"/><ellipse cx="102" cy="62" rx="11" ry="17" transform="rotate(28 102 62)" stroke="#c4b5a5" stroke-width="1.2"/><circle cx="78" cy="48" r="14" stroke="#d4c4b4" stroke-width="1.2"/><path d="M78 34 C90 28 102 34 104 46" stroke="#d4c4b4" stroke-width="1.1" stroke-linecap="round"/></svg>`)}`;

export function DecorativeLineArt({ visible }: { visible: boolean }) {
  if (!visible) return null;
  return (
    <Image
      testID="sidebar-line-art"
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      source={{ uri: HERB_ART }}
      style={styles.art}
    />
  );
}

const styles = StyleSheet.create({
  art: { width: 120, height: 160, opacity: 0.55, marginTop: "auto" },
});
