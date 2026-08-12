import { Slot, useRouter } from "expo-router";
import { useMemo } from "react";

import { createIngestionApi } from "../../src/api/ingestion";
import { useApi } from "../../src/api/ApiProvider";
import { useAuth } from "../../src/auth/AuthProvider";
import { AppShell } from "../../src/components/AppShell";
import { AuthGate } from "../../src/auth/AuthGate";
import { ImportSessionProvider } from "../../src/imports/ImportSessionProvider";

export default function AuthenticatedLayout() {
  const { client } = useApi();
  const auth = useAuth();
  const router = useRouter();
  const ingestion = useMemo(() => createIngestionApi(client), [client]);
  const onUnauthorized = () => {
    void auth.logout().catch(() => undefined);
    router.replace("/");
  };
  return <AuthGate><ImportSessionProvider ingestion={ingestion} onUnauthorized={onUnauthorized}><AppShell><Slot /></AppShell></ImportSessionProvider></AuthGate>;
}
