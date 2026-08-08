import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native";

import { ApiUnauthorizedError } from "../api/client";
import type { createIngestionApi, ImportJob, ImportJobStatus } from "../api/ingestion";
import { colors, sharedStyles } from "../theme";

const TERMINAL_STATUSES: ReadonlySet<ImportJobStatus> = new Set([
  "completed",
  "review_required",
  "failed",
  "cancelled",
  "timed_out",
]);

type ImportTab = "url" | "text";

export type ImportScreenProps = {
  ingestion: ReturnType<typeof createIngestionApi>;
  onBack(): void;
  onUnauthorized(): void;
};

function formatStatus(job: Pick<ImportJob, "status" | "errorCategory">): string {
  if (job.errorCategory) {
    return `${job.status} (${job.errorCategory})`;
  }
  return job.status;
}

export function ImportScreen({
  ingestion,
  onBack,
  onUnauthorized,
}: ImportScreenProps) {
  const [tab, setTab] = useState<ImportTab>("url");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
      }
    };
  }, []);

  const stopPolling = () => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  };

  const startPolling = (jobId: string) => {
    stopPolling();
    setStatusText("queued");

    const tick = async () => {
      try {
        const job = await ingestion.getImport(jobId);
        setStatusText(formatStatus(job));
        if (TERMINAL_STATUSES.has(job.status)) {
          stopPolling();
          setSubmitting(false);
        }
      } catch (err) {
        stopPolling();
        setSubmitting(false);
        if (err instanceof ApiUnauthorizedError) {
          onUnauthorized();
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to poll import status");
      }
    };

    void tick();
    pollTimer.current = setInterval(() => {
      void tick();
    }, 2000);
  };

  const submit = async () => {
    setError(null);
    setStatusText(null);

    if (tab === "url" && !url.trim()) {
      setError("URL is required.");
      return;
    }
    if (tab === "text" && !text.trim()) {
      setError("Recipe text is required.");
      return;
    }

    setSubmitting(true);
    try {
      const submission =
        tab === "url"
          ? await ingestion.createUrlImport(url.trim())
          : await ingestion.createTextImport(text.trim());
      startPolling(submission.jobId);
    } catch (err) {
      setSubmitting(false);
      if (err instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to start import");
    }
  };

  return (
    <ScrollView style={sharedStyles.screen} contentContainerStyle={{ paddingBottom: 40 }}>
      <Pressable accessibilityRole="button" onPress={onBack} style={sharedStyles.buttonSecondary}>
        <Text style={sharedStyles.buttonText}>Back to list</Text>
      </Pressable>

      <Text style={[sharedStyles.heading, { marginTop: 16 }]}>Import recipe</Text>

      <View style={sharedStyles.buttonRow}>
        <Pressable
          accessibilityRole="button"
          onPress={() => setTab("url")}
          style={tab === "url" ? sharedStyles.button : sharedStyles.buttonSecondary}
        >
          <Text style={sharedStyles.buttonText}>URL</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          onPress={() => setTab("text")}
          style={tab === "text" ? sharedStyles.button : sharedStyles.buttonSecondary}
        >
          <Text style={sharedStyles.buttonText}>Text</Text>
        </Pressable>
      </View>

      {tab === "url" ? (
        <>
          <Text style={sharedStyles.label}>Recipe URL</Text>
          <TextInput
            value={url}
            onChangeText={setUrl}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="https://example.com/recipe"
            placeholderTextColor={colors.note}
            style={sharedStyles.input}
          />
        </>
      ) : (
        <>
          <Text style={sharedStyles.label}>Recipe text</Text>
          <TextInput
            value={text}
            onChangeText={setText}
            multiline
            numberOfLines={10}
            placeholder="Paste recipe text…"
            placeholderTextColor={colors.note}
            style={[sharedStyles.input, { minHeight: 160, textAlignVertical: "top" }]}
          />
        </>
      )}

      {statusText ? (
        <Text style={sharedStyles.note}>Status: {statusText}</Text>
      ) : null}
      {error ? <Text style={sharedStyles.error}>{error}</Text> : null}

      <Pressable
        accessibilityRole="button"
        disabled={submitting}
        onPress={() => void submit()}
        style={sharedStyles.button}
      >
        {submitting ? (
          <ActivityIndicator color={colors.badge} />
        ) : (
          <Text style={sharedStyles.buttonText}>Start import</Text>
        )}
      </Pressable>
    </ScrollView>
  );
}
