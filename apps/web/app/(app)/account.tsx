import { useCallback, useMemo, useRef, useState } from "react";
import { Platform } from "react-native";
import { useRouter } from "expo-router";

import { createCatalogApi } from "../../src/api/catalog";
import { createIngestionApi } from "../../src/api/ingestion";
import { ApiUnauthorizedError } from "../../src/api/client";
import { useApi } from "../../src/api/ApiProvider";
import { useAuth } from "../../src/auth/AuthProvider";
import { clearAllCookingSessions } from "../../src/cooking/session";
import { downloadBlob, exportCookbookZip } from "../../src/export/cookbookExport";
import { AccountScreen, EXPORT_ERROR_COPY } from "../../src/screens/AccountScreen";

export default function AccountRoute() {
  const auth = useAuth();
  const { accountDeletionClient, client } = useApi();
  const router = useRouter();
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const exportInFlight = useRef(false);
  const deletionCatalog = useMemo(() => createCatalogApi(accountDeletionClient), [accountDeletionClient]);
  const catalog = useMemo(() => createCatalogApi(client), [client]);
  const ingestion = useMemo(() => createIngestionApi(client), [client]);

  const onUnauthorized = useCallback(() => {
    router.replace("/");
  }, [router]);

  const onExportCookbook = useCallback(async () => {
    if (exportInFlight.current) return;
    exportInFlight.current = true;
    setExportBusy(true);
    setExportError(null);
    try {
      const result = await exportCookbookZip({
        catalog,
        ingestion,
        profile: {
          name: auth.user?.name ?? null,
          email: auth.user?.email ?? null,
        },
      });
      downloadBlob(result.blob, result.fileName);
    } catch (error) {
      if (error instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setExportError(EXPORT_ERROR_COPY);
    } finally {
      exportInFlight.current = false;
      setExportBusy(false);
    }
  }, [auth.user?.email, auth.user?.name, catalog, ingestion, onUnauthorized]);

  return (
    <AccountScreen
      identity={auth.user}
      onLogout={async () => {
        try {
          await auth.logout();
        } finally {
          router.replace("/");
        }
      }}
      onDeleteAccount={async () => {
        await deletionCatalog.requestAccountDeletion();
        clearAllCookingSessions();
        try {
          await auth.logout();
        } finally {
          router.replace("/");
        }
      }}
      onExportCookbook={onExportCookbook}
      showExportCookbook={Platform.OS === "web"}
      exportBusy={exportBusy}
      exportError={exportError}
    />
  );
}
