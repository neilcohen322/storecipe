import { useCallback, useRef, useState } from "react";
import { useFocusEffect } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import { ApiError, ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import type { createIngestionApi, ImportHistoryItem, ImportHistoryPhase } from "../api/ingestion";
import { Button, EmptyState, ErrorState, InlineNotice, LoadingState, OfflineBanner, PageHeader, Screen, Section } from "../components";
import { useTheme } from "../theme/ThemeProvider";

export type ImportHistoryScreenProps = {
  ingestion: ReturnType<typeof createIngestionApi>;
  onNewImport(): void;
  onUnauthorized(): void;
};

const PHASE_LABELS: Record<ImportHistoryPhase, string> = {
  waiting: "Waiting",
  fetching: "Fetching",
  extracting: "Extracting",
  validating: "Validating",
  saving: "Saving",
  completed: "Complete",
  review_required: "Review needed",
  failed: "Failed",
  cancelled: "Cancelled",
  timed_out: "Timed out",
};

const ACTIVE_PHASES = new Set<ImportHistoryPhase>([
  "waiting",
  "fetching",
  "extracting",
  "validating",
  "saving",
]);

function isOfflineError(error: unknown): boolean {
  return error instanceof ApiNetworkError
    || (typeof error === "object" && error !== null && ((error as { code?: unknown }).code === "ERR_NETWORK" || (error as { code?: unknown }).code === "NETWORK_ERROR"));
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function kindLabel(kind: ImportHistoryItem["inputKind"]): string {
  return kind === "url" ? "URL" : "Text";
}

export function ImportHistoryScreen({ ingestion, onNewImport, onUnauthorized }: ImportHistoryScreenProps) {
  const { theme } = useTheme();
  const [items, setItems] = useState<ImportHistoryItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<"none" | "offline" | "generic">("none");
  const [actionError, setActionError] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const mounted = useRef(true);
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const cancelling = useRef(new Set<string>());
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  const load = useCallback(async (cursor: string | null = null) => {
    const pagination = cursor !== null;
    const id = ++requestId.current;
    if (!pagination) {
      controller.current?.abort();
      const nextController = new AbortController();
      controller.current = nextController;
      setError("none");
    } else {
      setLoadingMore(true);
    }
    try {
      const page = await ingestion.listImports(
        { ...(cursor ? { cursor } : {}), limit: 20 },
        { signal: pagination ? undefined : controller.current?.signal },
      );
      if (!mounted.current || id !== requestId.current) return;
      setItems((current) => (pagination
        ? [...new Map([...current, ...page.items].map((item) => [item.id, item])).values()]
        : page.items));
      setNextCursor(page.nextCursor);
    } catch (caught) {
      if (!mounted.current || id !== requestId.current || (caught instanceof Error && caught.name === "AbortError")) return;
      if (caught instanceof ApiUnauthorizedError) {
        onUnauthorizedRef.current();
        return;
      }
      setError(isOfflineError(caught) ? "offline" : "generic");
    } finally {
      if (mounted.current && id === requestId.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [ingestion]);

  useFocusEffect(useCallback(() => {
    mounted.current = true;
    setLoading(true);
    setActionError(null);
    void load();
    return () => {
      mounted.current = false;
      controller.current?.abort();
      requestId.current += 1;
    };
  }, [load]));

  const cancel = async (item: ImportHistoryItem) => {
    if (cancelling.current.has(item.id)) return;
    cancelling.current.add(item.id);
    setCancellingId(item.id);
    setActionError(null);
    try {
      await ingestion.cancelImport(item.id);
      if (!mounted.current) return;
      void load();
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiUnauthorizedError) {
        onUnauthorizedRef.current();
        return;
      }
      if (caught instanceof ApiError && caught.status === 409) {
        setActionError("This import can no longer be cancelled.");
        void load();
        return;
      }
      setActionError("We couldn't cancel this import. Please try again.");
    } finally {
      cancelling.current.delete(item.id);
      if (mounted.current) setCancellingId(null);
    }
  };

  const errorContent = error === "offline"
    ? <><OfflineBanner message="You’re offline. Check your connection and try again." /><Button label="Try again" onPress={() => { setLoading(true); void load(); }} /></>
    : <ErrorState title="We couldn't load your imports. Please try again." action={<Button label="Try again" onPress={() => { setLoading(true); void load(); }} />} />;

  return (
    <Screen>
      <PageHeader title="Imports" />
      <Button label="Import a recipe" onPress={onNewImport} />
      {loading && items.length === 0
        ? <LoadingState label="Loading imports" />
        : error !== "none" && items.length === 0
          ? errorContent
          : items.length === 0
            ? <EmptyState title="No imports yet." description="Imports you start will appear here." />
            : (
              <Section title="Import history">
                {actionError ? <InlineNotice tone="error" message={actionError} /> : null}
                {items.map((item) => {
                  const phaseLabel = PHASE_LABELS[item.phase];
                  const cancellable = ACTIVE_PHASES.has(item.phase);
                  return (
                    <View key={item.id} style={[styles.row, { borderColor: theme.colors.border }]}>
                      <Text style={[styles.kind, { color: theme.colors.text }]}>{kindLabel(item.inputKind)}</Text>
                      <Text style={[styles.phase, { color: theme.colors.mutedText }]}>{phaseLabel}</Text>
                      <Text style={[styles.meta, { color: theme.colors.mutedText }]}>Started {formatTimestamp(item.createdAt)}</Text>
                      {item.terminalAt ? (
                        <Text style={[styles.meta, { color: theme.colors.mutedText }]}>Finished {formatTimestamp(item.terminalAt)}</Text>
                      ) : null}
                      {cancellable ? (
                        <Button
                          testID={`cancel-import-${item.id}`}
                          label="Cancel import"
                          variant="secondary"
                          loading={cancellingId === item.id}
                          disabled={cancellingId === item.id}
                          onPress={() => void cancel(item)}
                        />
                      ) : null}
                    </View>
                  );
                })}
              </Section>
            )}
      {nextCursor ? <Button label="Load more imports" loading={loadingMore} onPress={() => void load(nextCursor)} /> : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: { gap: 4, paddingVertical: 12, borderBottomWidth: 1 },
  kind: { fontSize: 16, fontWeight: "700" },
  phase: { fontSize: 14 },
  meta: { fontSize: 12 },
});
