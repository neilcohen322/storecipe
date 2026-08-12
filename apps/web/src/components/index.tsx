import React, { cloneElement, forwardRef, isValidElement, useEffect, useRef, useState, type ComponentProps, type ReactElement, type ReactNode } from "react";
import { ActivityIndicator, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View, type DimensionValue, type StyleProp, type TextStyle, type ViewStyle } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useTheme } from "../theme/ThemeProvider";
import { EmptyState } from "./EmptyState";
import { InlineNotice } from "./InlineNotice";

export { EmptyState } from "./EmptyState";
export { InlineNotice } from "./InlineNotice";

type ChildProps = { style?: StyleProp<TextStyle>; accessibilityLabel?: string; accessibilityState?: object; nativeID?: string; "aria-describedby"?: string };
type InputModality = "keyboard" | "pointer";
type Focusable = { focus: () => void };

let inputModality: InputModality = "keyboard";
const markKeyboardModality = () => { inputModality = "keyboard"; };
const markPointerModality = () => { inputModality = "pointer"; };

function useInputModality() {
  useEffect(() => {
    if (Platform.OS !== "web" || typeof document === "undefined") return;
    document.addEventListener("keydown", markKeyboardModality, true);
    document.addEventListener("pointerdown", markPointerModality, true);
    return () => {
      document.removeEventListener("keydown", markKeyboardModality, true);
      document.removeEventListener("pointerdown", markPointerModality, true);
    };
  }, []);
  return { isKeyboard: () => inputModality === "keyboard", markKeyboard: markKeyboardModality, markPointer: markPointerModality };
}

function matchesFocusVisible(target: unknown): boolean | undefined {
  if (typeof target !== "object" || target === null) return undefined;
  const matches = Reflect.get(target, "matches");
  return typeof matches === "function" ? Reflect.apply(matches, target, [":focus-visible"]) === true : undefined;
}
const webFocus = (color: string): ViewStyle => ({ outlineStyle: "solid", outlineWidth: 2, outlineColor: color, outlineOffset: 2 });

export type ButtonProps = Omit<ComponentProps<typeof Pressable>, "children"> & { label: string; variant?: "primary" | "secondary" | "quiet" | "icon" | "danger"; loading?: boolean; icon?: ReactNode };
export const Button = forwardRef<React.ElementRef<typeof Pressable>, ButtonProps>(function Button({ label, variant = "primary", loading = false, disabled, icon, style, onPress, onFocus, onBlur, onPointerDown, ...props }, ref) {
  const { theme } = useTheme();
  const modality = useInputModality();
  const [focusVisible, setFocusVisible] = useState(false);
  const [hovered, setHovered] = useState(false);
  const colors = theme.colors;
  const bg = variant === "primary" ? colors.accent : variant === "danger" ? colors.danger : variant === "secondary" ? colors.surface : "transparent";
  const fg = variant === "primary" || variant === "danger" ? colors.accentContrast : colors.text;
  return <Pressable {...props} ref={ref} onPointerDown={(event) => { modality.markPointer(); onPointerDown?.(event); }} onHoverIn={() => setHovered(true)} onHoverOut={() => setHovered(false)} onPress={disabled || loading ? undefined : onPress} onFocus={(event) => { setFocusVisible(matchesFocusVisible(event.currentTarget) ?? modality.isKeyboard()); onFocus?.(event); }} onBlur={(event) => { setFocusVisible(false); onBlur?.(event); }} accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: disabled || loading, busy: loading }} disabled={disabled || loading} focusable style={(state) => { const customStyle = typeof style === "function" ? style(state) : style; return [styles.button, { backgroundColor: hovered ? colors.accentHover : bg, borderColor: variant === "quiet" ? "transparent" : colors.border, opacity: state.pressed ? 0.76 : 1 }, variant === "icon" && styles.iconButton, customStyle, focusVisible && webFocus(colors.focusRing)]; }}><>{loading ? <ActivityIndicator color={fg} /> : icon}{variant !== "icon" && <Text style={[styles.buttonText, { color: fg, fontSize: theme.type.body }]}>{label}</Text>}</></Pressable>;
});

export type FieldProps = { label: string; hint?: string; error?: string; control: ReactElement<ChildProps> };
export function Field({ label, hint, error, control }: FieldProps) {
  const id = `field-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  const describedBy = `${id}-description`;
  const { theme } = useTheme();
  const controlProps: ChildProps = { nativeID: id, accessibilityLabel: label, accessibilityState: { invalid: !!error }, "aria-describedby": describedBy, style: [control.props.style, styles.fieldControl, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border, color: theme.colors.text, fontSize: theme.type.body }] };
  return <View style={styles.field}><Text nativeID={`${id}-label`} style={[styles.label, { color: theme.colors.text }]}>{label}</Text>{isValidElement(control) ? cloneElement(control, controlProps) : control}{(hint || error) && <Text nativeID={describedBy} accessibilityRole={error ? "alert" : undefined} accessibilityLiveRegion={error ? "assertive" : undefined} style={[styles.hint, { color: error ? theme.colors.danger : theme.colors.mutedText }]}>{error || hint}</Text>}</View>;
}

export type TextAreaProps = Omit<ComponentProps<typeof TextInput>, "style"> & { label: string; hint?: string; error?: string };
export function TextArea({ label, hint, error, ...props }: TextAreaProps) { const { theme } = useTheme(); return <Field label={label} hint={hint} error={error} control={<TextInput {...props} multiline textAlignVertical="top" style={[styles.textarea, { color: theme.colors.text, backgroundColor: theme.colors.surface, borderColor: theme.colors.border, fontSize: theme.type.body }]} />} />; }

export function Screen({ children, ...props }: ComponentProps<typeof ScrollView>) { const { theme } = useTheme(); const insets = useSafeAreaInsets(); return <ScrollView {...props} style={[{ backgroundColor: theme.colors.canvas }, props.style]} contentContainerStyle={[styles.screen, { paddingTop: insets.top + theme.spacing.md, paddingRight: insets.right + theme.spacing.md, paddingBottom: insets.bottom + theme.spacing.md, paddingLeft: insets.left + theme.spacing.md }, props.contentContainerStyle]}><View testID="screen-content" style={[styles.readable, { maxWidth: 1120 }]}>{children}</View></ScrollView>; }
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) { const { theme } = useTheme(); return <View style={styles.pageHeader}><View style={{ flex: 1 }}><Text accessibilityRole="header" style={[styles.heading, { color: theme.colors.text, fontSize: theme.type.heading }]}>{title}</Text>{subtitle && <Text style={[styles.hint, { color: theme.colors.mutedText }]}>{subtitle}</Text>}</View>{actions}</View>; }
export function Section({ title, children, accessibilityRole, accessibilityLabel, style, ...props }: ComponentProps<typeof View> & { title?: string }) {
  const { theme } = useTheme();
  const heading = title ? <Text accessibilityRole="header" style={[styles.sectionTitle, { color: theme.colors.text }]}>{title}</Text> : null;
  if (accessibilityRole === "list" && heading) {
    return <View {...props} style={[{ marginBottom: theme.spacing.lg }, style]}>{heading}<View accessibilityRole={accessibilityRole} accessibilityLabel={accessibilityLabel}>{children}</View></View>;
  }
  return <View {...props} accessibilityRole={accessibilityRole} accessibilityLabel={accessibilityLabel} style={[{ marginBottom: theme.spacing.lg }, style]}>{heading}{children}</View>;
}
export function ResponsiveGrid({ children, minItemWidth = 260, ...props }: ComponentProps<typeof View> & { minItemWidth?: number }) { return <View {...props} style={[styles.grid, props.style]}>{React.Children.map(children, child => <View style={{ flexGrow: 1, flexBasis: minItemWidth }}>{child}</View>)}</View>; }
export function LoadingState({ label = "Loading" }: { label?: string }) { const { theme } = useTheme(); return <View accessibilityRole="progressbar" accessibilityLabel={label} style={styles.state}><ActivityIndicator color={theme.colors.accent} /><Text style={[styles.hint, { color: theme.colors.mutedText }]}>{label}</Text></View>; }
export function ErrorState({ title = "Something went wrong", description, action }: { title?: string; description?: string; action?: ReactNode }) { const { theme } = useTheme(); return <View accessibilityRole="alert" style={styles.state}><Text accessibilityRole="header" style={[styles.stateTitle, { color: theme.colors.text }]}>{title}</Text>{description && <Text style={[styles.hint, { color: theme.colors.mutedText }]}>{description}</Text>}{action}</View>; }
export function OfflineBanner({ message = "You’re offline. Changes will sync when you reconnect." }: { message?: string }) { return <InlineNotice message={message} tone="warning" />; }
export function StatusBadge({ status, label }: { status: "success" | "warning" | "error" | "info" | string; label?: string }) { const { theme } = useTheme(); return <View accessibilityRole="text" aria-live="polite" style={[styles.badge, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}><Text style={{ color: theme.colors.text }}>{label || status}</Text></View>; }

export type ImportStatus = "queued" | "processing" | "completed" | "review_required" | "failed" | "cancelled" | "timed_out";
const importLabels: Record<ImportStatus, string> = { queued: "Waiting", processing: "Working", completed: "Complete", review_required: "Review needed", failed: "Failed", cancelled: "Cancelled", timed_out: "Timed out" };
export function ImportProgress({ status }: { status: ImportStatus }) { return <View testID="import-progress" accessibilityRole="text" aria-live="polite" accessibilityLabel={`Import status: ${importLabels[status]}`} style={styles.state}><Text testID="import-progress-label">{importLabels[status]}</Text></View>; }
export function RatingControl({ value, onChange, max = 5, disabled = false }: { value: number; onChange: (value: number) => void; max?: number; disabled?: boolean }) { const { theme } = useTheme(); const modality = useInputModality(); const [focusedRating, setFocusedRating] = useState<number | null>(null); const [hoveredRating, setHoveredRating] = useState<number | null>(null); return <View accessibilityRole="radiogroup" accessibilityLabel="Rating" accessibilityState={{ disabled }}><View style={styles.rating}>{Array.from({ length: max }, (_, index) => { const rating = index + 1; return <Pressable key={rating} disabled={disabled} onPointerDown={modality.markPointer} onHoverIn={() => setHoveredRating(rating)} onHoverOut={() => setHoveredRating(null)} accessibilityRole="button" accessibilityLabel={`Rate ${rating} out of ${max}`} accessibilityState={{ selected: rating === value, disabled }} onFocus={(event) => setFocusedRating((matchesFocusVisible(event.currentTarget) ?? modality.isKeyboard()) ? rating : null)} onBlur={() => setFocusedRating(null)} onPress={disabled ? undefined : () => onChange(rating)} focusable style={({ pressed }) => [styles.ratingButton, disabled && { opacity: 0.55 }, hoveredRating === rating && !disabled && { backgroundColor: theme.colors.accentHover }, pressed && { opacity: 0.76 }, focusedRating === rating && webFocus(theme.colors.focusRing)]}><Text style={{ color: theme.colors.text }}>{rating <= value ? "★" : "☆"}</Text></Pressable>; })}</View></View>; }
export function Toast({ message, visible = true }: { message: string; visible?: boolean }) { const { theme } = useTheme(); if (!visible) return null; return <View accessibilityRole="alert" style={[styles.toast, { backgroundColor: theme.colors.elevatedSurface, borderColor: theme.colors.border }]}><Text style={{ color: theme.colors.text }}>{message}</Text></View>; }
export function ConfirmDialog({ visible, title, description, onConfirm, onCancel, initialFocusRef, returnFocusRef }: { visible: boolean; title: string; description?: string; onConfirm: () => void; onCancel: () => void; initialFocusRef?: React.RefObject<Focusable | null>; returnFocusRef?: React.RefObject<Focusable | null> }) { const { theme } = useTheme(); const confirmRef = useRef<React.ElementRef<typeof Pressable>>(null); const titleId = "confirm-dialog-title"; const descriptionId = "confirm-dialog-description"; useEffect(() => { if (visible) { markKeyboardModality(); (initialFocusRef?.current ?? confirmRef.current)?.focus(); } else returnFocusRef?.current?.focus(); }, [initialFocusRef, returnFocusRef, visible]); return <Modal testID="confirm-dialog" visible={visible} transparent accessibilityViewIsModal onRequestClose={onCancel}><View style={[styles.scrim, { backgroundColor: theme.colors.scrim }]}><View testID="confirm-dialog-panel" role="dialog" accessible accessibilityLabel={title} accessibilityHint={description} style={[styles.dialog, { backgroundColor: theme.colors.elevatedSurface }]} accessibilityViewIsModal aria-modal={true} aria-labelledby={titleId} aria-describedby={description ? descriptionId : undefined}><Text nativeID={titleId} accessibilityRole="header" style={[styles.stateTitle, { color: theme.colors.text }]}>{title}</Text>{description && <Text nativeID={descriptionId} style={{ color: theme.colors.mutedText }}>{description}</Text>}<View style={styles.actions}><Button label="Cancel" variant="secondary" onPress={onCancel} /><Button ref={confirmRef} label="Confirm" variant="danger" onPress={onConfirm} /></View></View></View></Modal>; }
export function Skeleton({ width = "100%", height = 20 }: { width?: DimensionValue; height?: number }) { const { theme } = useTheme(); return <View testID="skeleton" accessibilityLabel="Loading content" style={{ width, height, backgroundColor: theme.colors.border, borderRadius: theme.radii.sm }} />; }
export function RecipeMedia({ title, tags = [] }: { title: string; tags?: string[] }) { const { theme } = useTheme(); const initials = title.trim().split(/\s+/).slice(0, 2).map(word => word[0]).join("").toUpperCase() || "R"; const seed = `${title}${tags.join("")}`.split("").reduce((sum, char) => sum + char.charCodeAt(0), 0); const colors = [theme.colors.accent, theme.colors.success, theme.colors.warning, theme.colors.danger]; return <View testID="recipe-media" accessibilityRole="image" accessibilityLabel={`${title} recipe placeholder`} style={[styles.media, { backgroundColor: colors[seed % colors.length] }]}><Text style={[styles.mediaText, { color: theme.colors.accentContrast }]}>{initials}</Text></View>; }

const styles = StyleSheet.create({ button: { minHeight: 44, minWidth: 44, borderWidth: 1, borderRadius: 12, paddingHorizontal: 16, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8 }, iconButton: { width: 44, paddingHorizontal: 0 }, buttonText: { fontWeight: "600" }, field: { gap: 8, marginBottom: 16 }, fieldControl: { minHeight: 44, minWidth: 44, borderWidth: 1, borderRadius: 8, paddingHorizontal: 12 }, label: { fontSize: 15, fontWeight: "600" }, hint: { fontSize: 12 }, textarea: { minHeight: 120, minWidth: 44, borderWidth: 1, borderRadius: 8, padding: 12 }, screen: { flexGrow: 1, alignItems: "center" }, readable: { width: "100%" }, pageHeader: { flexDirection: "row", alignItems: "center", gap: 16, marginBottom: 24 }, heading: { fontWeight: "700" }, sectionTitle: { fontSize: 18, fontWeight: "700", marginBottom: 12 }, grid: { flexDirection: "row", flexWrap: "wrap", gap: 16 }, state: { minHeight: 44, padding: 16, alignItems: "center", justifyContent: "center", gap: 8 }, stateTitle: { fontSize: 18, fontWeight: "700" }, badge: { minHeight: 44, minWidth: 44, paddingHorizontal: 12, borderWidth: 1, borderRadius: 999, alignItems: "center", justifyContent: "center" }, rating: { flexDirection: "row" }, ratingButton: { minWidth: 44, minHeight: 44, alignItems: "center", justifyContent: "center" }, toast: { minHeight: 44, padding: 12, borderRadius: 8 }, scrim: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }, dialog: { width: "100%", maxWidth: 480, gap: 16, padding: 24, borderRadius: 16 }, actions: { flexDirection: "row", justifyContent: "flex-end", gap: 8 }, media: { minHeight: 160, alignItems: "center", justifyContent: "center", borderRadius: 16 }, mediaText: { fontSize: 36, fontWeight: "800" } });
