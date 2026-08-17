import { useState } from "react";
import { TextInput, View } from "react-native";

import { Button, Field, InlineNotice, PageHeader, Screen, Section, TextArea } from "../components";
import { useImportSession } from "../imports/ImportSessionProvider";
import { getImportPresentation } from "../utils/importPolling";

type ImportTab = "url" | "text";
export type ImportScreenProps = { onBack(): void; onContinueExtractedRecipe(jobId: string): void };

function terminalCopy(summary: NonNullable<ReturnType<typeof useImportSession>["terminalSummary"]>): string {
  switch (summary.status) {
    case "completed":
      return "Your recipe import is complete.";
    case "review_required":
      if (summary.errorCategory === "daily_ai_budget_exceeded") {
        return summary.hasCandidate
          ? "Today's AI budget is used up, so we couldn't finish saving. Continue with the extracted recipe to check it and save."
          : "Today's AI budget is used up, so this recipe wasn't extracted. Try again later, or paste the recipe as text.";
      }
      if (summary.hasCandidate) {
        return "The recipe was extracted but couldn't be saved automatically. Continue with the extracted recipe to check it and save.";
      }
      return "This import needs attention, but no extracted recipe is available. You can enter it manually or retry.";
    case "failed":
      return "This import failed.";
    case "cancelled":
      return "This import was cancelled.";
    case "timed_out":
      return "This import took too long and stopped.";
  }
}

export function ImportScreen({ onBack, onContinueExtractedRecipe }: ImportScreenProps) {
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
  return <Screen>
    <Button label="Back to imports" variant="secondary" onPress={onBack} />
    <PageHeader title="Import recipe" subtitle="Some imports may take longer than others." />
    <Section>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 10 }}>
        <Button label="URL" variant={tab === "url" ? "primary" : "secondary"} onPress={() => setTab("url")} />
        <Button label="Text" variant={tab === "text" ? "primary" : "secondary"} onPress={() => setTab("text")} />
      </View>
    </Section>
    {tab === "url" ? <Field label="Recipe URL" control={<TextInput value={url} onChangeText={setUrl} autoCapitalize="none" autoCorrect={false} placeholder="https://example.com/recipe" />} /> : <TextArea label="Recipe text" value={text} onChangeText={setText} numberOfLines={10} placeholder="Paste recipe text…" />}
    {activePresentation ? <InlineNotice tone="info" message={activePresentation.label} /> : null}
    {session.terminalSummary ? <InlineNotice tone={session.terminalSummary.status === "completed" ? "success" : session.terminalSummary.status === "failed" ? "error" : session.terminalSummary.status === "review_required" || session.terminalSummary.status === "timed_out" ? "warning" : "info"} message={terminalCopy(session.terminalSummary)} /> : null}
    {session.terminalSummary?.status === "review_required" && session.terminalSummary.hasCandidate ? <Button label="Continue with extracted recipe" onPress={() => { const jobId = session.terminalSummary?.jobId; if (typeof onContinueExtractedRecipe === "function" && jobId) onContinueExtractedRecipe(jobId); }} /> : null}
    {session.terminalSummary?.canRetry ? <Button label="Retry import" variant="secondary" onPress={() => void session.retryImport()} /> : null}
    {validationError ? <InlineNotice tone="error" message={validationError} /> : null}
    {session.error ? <InlineNotice tone="error" message={session.error} /> : null}
    <Button label="Start import" loading={session.isStarting} onPress={submit} />
  </Screen>;
}
