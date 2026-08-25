import { useRef, useState } from "react";
import { Text, TextInput } from "react-native";

import { accountDeletionScopeErrorMessage } from "../api/client";
import { Button, Field, InlineNotice, PageHeader, Screen, Section } from "../components";
import { ThemeControl } from "../components/ThemeControl";
import { useTheme } from "../theme/ThemeProvider";

const DELETE_CONFIRMATION = "DELETE MY ACCOUNT";
const EXPORT_ERROR_COPY = "We couldn't export your cookbook. Please try again.";

export type AccountScreenProps = {
  identity: { name?: string; email?: string; picture?: string } | null;
  onLogout(): Promise<void>;
  onDeleteAccount(): Promise<void>;
  onExportCookbook?: () => Promise<void>;
  showExportCookbook?: boolean;
  exportBusy?: boolean;
  exportError?: string | null;
};

export function AccountScreen({
  identity,
  onLogout,
  onDeleteAccount,
  onExportCookbook,
  showExportCookbook = false,
  exportBusy = false,
  exportError = null,
}: AccountScreenProps) {
  const { theme } = useTheme();
  const [confirmation, setConfirmation] = useState("");
  const [isDeleting, setIsDeleting] = useState(false);
  const [deletionError, setDeletionError] = useState<string | null>(null);
  const deletionInFlight = useRef(false);
  const exportInFlight = useRef(false);
  const canDelete = confirmation === DELETE_CONFIRMATION && !isDeleting;
  const showExport = showExportCookbook && onExportCookbook !== undefined;

  const deleteAccount = async () => {
    if (!canDelete || deletionInFlight.current) return;
    deletionInFlight.current = true;
    setIsDeleting(true);
    setDeletionError(null);
    try {
      await onDeleteAccount();
    } catch (error) {
      setDeletionError(accountDeletionScopeErrorMessage(error) ?? "We could not start account deletion. Please try again.");
    } finally {
      deletionInFlight.current = false;
      setIsDeleting(false);
    }
  };

  const exportCookbook = async () => {
    if (!onExportCookbook || exportBusy || exportInFlight.current) return;
    exportInFlight.current = true;
    try {
      await onExportCookbook();
    } finally {
      exportInFlight.current = false;
    }
  };

  return <Screen>
    <PageHeader title="Account" />
    <Section title="Signed in as">
      <Text style={[styles.name, { color: theme.colors.text }]}>{identity?.name ?? "Storecipe member"}</Text>
      {identity?.email ? <Text style={{ color: theme.colors.mutedText }}>{identity.email}</Text> : null}
    </Section>
    <Section title="Theme"><ThemeControl /></Section>
    {showExport ? (
      <Section title="Export cookbook">
        <Text style={[styles.copy, { color: theme.colors.mutedText }]}>
          Download a ZIP copy of your recipes, cover images, and import history. Export runs entirely in your browser.
        </Text>
        {exportError ? <InlineNotice tone="error" message={exportError} /> : null}
        <Button
          label={exportError ? "Try export again" : "Export cookbook"}
          variant="secondary"
          onPress={() => void exportCookbook()}
          disabled={exportBusy}
          loading={exportBusy}
        />
      </Section>
    ) : null}
    <Section title="Delete account">
      <Text style={[styles.copy, { color: theme.colors.mutedText }]}>This permanently starts deletion of your Storecipe account and cookbook. Type {DELETE_CONFIRMATION} exactly to continue.</Text>
      <Field
        label={`Type ${DELETE_CONFIRMATION} to confirm`}
        error={confirmation.length > 0 && confirmation !== DELETE_CONFIRMATION ? `Enter ${DELETE_CONFIRMATION} exactly.` : undefined}
        control={<TextInput value={confirmation} onChangeText={setConfirmation} autoCapitalize="characters" autoCorrect={false} editable={!isDeleting} />}
      />
      {deletionError ? <InlineNotice tone="error" message={deletionError} /> : null}
      <Button label="Delete my account" variant="danger" onPress={() => void deleteAccount()} disabled={!canDelete} loading={isDeleting} />
    </Section>
    <Button label="Log out" variant="secondary" onPress={() => void onLogout()} />
  </Screen>;
}

const styles = {
  name: { fontSize: 18, fontWeight: "700" as const },
  copy: { marginBottom: 16, lineHeight: 20 },
};

export { EXPORT_ERROR_COPY };
