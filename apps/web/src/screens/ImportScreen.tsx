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
import type { createIngestionApi } from "../api/ingestion";
import { colors, sharedStyles } from "../theme";
import {
  resolveImportIdempotencyAttempt,
  type ImportIdempotencyAttempt,
} from "../utils/idempotencySession";
import { createImportPoller, type ImportPoller } from "../utils/importPolling";

type ImportTab = "url" | "text";

export type ImportScreenProps = {
  ingestion: ReturnType<typeof createIngestionApi>;
  onBack(): void;
  onUnauthorized(): void;
};

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
  const mountedRef = useRef(true);
  const attemptRef = useRef<ImportIdempotencyAttempt | null>(null);
  const ingestionRef = useRef(ingestion);
  const onUnauthorizedRef = useRef(onUnauthorized);
  const pollerRef = useRef<ImportPoller | null>(null);

  ingestionRef.current = ingestion;
  onUnauthorizedRef.current = onUnauthorized;

  if (pollerRef.current === null) {
    pollerRef.current = createImportPoller({
      getImport: (jobId) => ingestionRef.current.getImport(jobId),
      isActive: () => mountedRef.current,
      onStatus: (next) => setStatusText(next),
      onTerminal: () => {
        attemptRef.current = null;
        setSubmitting(false);
      },
      onUnauthorized: () => onUnauthorizedRef.current(),
      onError: (message) => {
        // Keep key/job so retry resumes the same attempt instead of duplicating.
        setError(message);
        setSubmitting(false);
      },
      isUnauthorizedError: (err) => err instanceof ApiUnauthorizedError,
    });
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      pollerRef.current?.stop();
    };
  }, []);

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

    const fingerprint =
      tab === "url" ? `url:${url.trim()}` : `text:${text.trim()}`;
    const attempt = resolveImportIdempotencyAttempt(attemptRef.current, fingerprint);
    attemptRef.current = attempt;

    setSubmitting(true);
    try {
      let jobId = attempt.jobId;
      if (jobId === null) {
        const submission =
          tab === "url"
            ? await ingestion.createUrlImport(url.trim(), {
                idempotencyKey: attempt.session.key,
              })
            : await ingestion.createTextImport(text.trim(), {
                idempotencyKey: attempt.session.key,
              });
        jobId = submission.jobId;
        attemptRef.current = { session: attempt.session, jobId };
      }
      if (!mountedRef.current) {
        return;
      }
      pollerRef.current?.start(jobId);
    } catch (err) {
      if (!mountedRef.current) {
        return;
      }
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
