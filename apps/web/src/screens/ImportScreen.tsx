import { useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, Text, TextInput, View } from "react-native";

import { useImportSession } from "../imports/ImportSessionProvider";
import { colors, sharedStyles } from "../theme";
import { getImportPresentation } from "../utils/importPolling";

type ImportTab = "url" | "text";
export type ImportScreenProps = { onBack(): void };

function terminalCopy(status: NonNullable<ReturnType<typeof useImportSession>["terminalSummary"]>["status"]): string {
  switch (status) {
    case "completed": return "Your recipe import is complete.";
    case "review_required": return "This import needs your review before it can be added.";
    case "failed": return "This import failed.";
    case "cancelled": return "This import was cancelled.";
    case "timed_out": return "This import took too long and stopped.";
  }
}

export function ImportScreen({ onBack }: ImportScreenProps) {
  const [tab, setTab] = useState<ImportTab>("url");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const session = useImportSession();
  const activePresentation = session.activeJob ? getImportPresentation(session.activeJob) : null;
  const submit = () => {
    const value = tab === "url" ? url : text;
    if (!value.trim()) { setValidationError(tab === "url" ? "URL is required." : "Recipe text is required."); return; }
    setValidationError(null);
    void session.startImport({ mode: tab, value });
  };
  return <ScrollView style={sharedStyles.screen} contentContainerStyle={{ paddingBottom: 40 }}>
    <Pressable accessibilityRole="button" onPress={onBack} style={sharedStyles.buttonSecondary}><Text style={sharedStyles.buttonText}>Back to imports</Text></Pressable>
    <Text style={[sharedStyles.heading, { marginTop: 16 }]}>Import recipe</Text>
    <Text style={sharedStyles.note}>Some imports may take longer than others.</Text>
    <View style={sharedStyles.buttonRow}>
      <Pressable accessibilityRole="button" onPress={() => setTab("url")} style={tab === "url" ? sharedStyles.button : sharedStyles.buttonSecondary}><Text style={sharedStyles.buttonText}>URL</Text></Pressable>
      <Pressable accessibilityRole="button" onPress={() => setTab("text")} style={tab === "text" ? sharedStyles.button : sharedStyles.buttonSecondary}><Text style={sharedStyles.buttonText}>Text</Text></Pressable>
    </View>
    {tab === "url" ? <><Text style={sharedStyles.label}>Recipe URL</Text><TextInput accessibilityLabel="Recipe URL" value={url} onChangeText={setUrl} autoCapitalize="none" autoCorrect={false} placeholder="https://example.com/recipe" placeholderTextColor={colors.note} style={sharedStyles.input} /></> : <><Text style={sharedStyles.label}>Recipe text</Text><TextInput accessibilityLabel="Recipe text" value={text} onChangeText={setText} multiline numberOfLines={10} placeholder="Paste recipe text…" placeholderTextColor={colors.note} style={[sharedStyles.input, { minHeight: 160, textAlignVertical: "top" }]} /></>}
    {activePresentation ? <View accessibilityLiveRegion="polite"><Text style={sharedStyles.note}>{activePresentation.label}</Text></View> : null}
    {session.terminalSummary ? <View accessibilityLiveRegion="polite"><Text style={sharedStyles.note}>{terminalCopy(session.terminalSummary.status)}</Text>{session.terminalSummary.canRetry ? <Pressable accessibilityRole="button" onPress={() => void session.retryImport()} style={sharedStyles.buttonSecondary}><Text style={sharedStyles.buttonText}>Retry import</Text></Pressable> : null}</View> : null}
    {validationError ? <Text style={sharedStyles.error}>{validationError}</Text> : null}
    {session.error ? <Text style={sharedStyles.error}>{session.error}</Text> : null}
    <Pressable accessibilityRole="button" disabled={session.isStarting} onPress={submit} style={sharedStyles.button}>{session.isStarting ? <ActivityIndicator color={colors.badge} /> : <Text style={sharedStyles.buttonText}>Start import</Text>}</Pressable>
  </ScrollView>;
}
