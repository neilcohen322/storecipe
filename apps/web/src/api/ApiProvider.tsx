import { createContext, type PropsWithChildren, useContext, useMemo } from "react";

import { createApiClient } from "./client";
import { getApiBases } from "./bases";
import { useAuth } from "../auth/AuthProvider";

type ApiContextValue = {
  client: ReturnType<typeof createApiClient>;
  accountDeletionClient: ReturnType<typeof createApiClient>;
};

const ApiContext = createContext<ApiContextValue | undefined>(undefined);

export function ApiProvider({ children }: PropsWithChildren) {
  const auth = useAuth();
  const bases = useMemo(() => getApiBases(), []);
  const client = useMemo(
    () => createApiClient(auth.getAccessToken, bases),
    [auth.getAccessToken, bases],
  );
  const accountDeletionClient = useMemo(
    () => createApiClient(auth.getAccountDeletionAccessToken, bases),
    [auth.getAccountDeletionAccessToken, bases],
  );

  const value = useMemo(() => ({ client, accountDeletionClient }), [accountDeletionClient, client]);
  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export function useApi(): ApiContextValue {
  const context = useContext(ApiContext);
  if (!context) {
    throw new Error("useApi must be used within ApiProvider");
  }
  return context;
}
