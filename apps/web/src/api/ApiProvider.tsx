import { createContext, type PropsWithChildren, useContext, useMemo } from "react";

import { createApiClient, isUnauthorizedCredentialError } from "./client";
import { getApiBases } from "./bases";
import { useAuth } from "../auth/AuthProvider";

type ApiContextValue = {
  client: ReturnType<typeof createApiClient>;
};

const ApiContext = createContext<ApiContextValue | undefined>(undefined);

export function ApiProvider({ children }: PropsWithChildren) {
  const auth = useAuth();
  const bases = useMemo(() => getApiBases(), []);
  const client = useMemo(
    () =>
      createApiClient(async () => {
        try {
          return await auth.getAccessToken();
        } catch (error) {
          if (isUnauthorizedCredentialError(error)) {
            await auth.login();
          }
          throw error;
        }
      }, bases),
    [auth, bases],
  );

  const value = useMemo(() => ({ client }), [client]);
  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export function useApi(): ApiContextValue {
  const context = useContext(ApiContext);
  if (!context) {
    throw new Error("useApi must be used within ApiProvider");
  }
  return context;
}
