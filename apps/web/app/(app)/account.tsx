import { useRouter } from "expo-router";

import { useAuth } from "../../src/auth/AuthProvider";
import { AccountScreen } from "../../src/screens/AccountScreen";

export default function AccountRoute() {
  const auth = useAuth();
  const router = useRouter();
  return <AccountScreen identity={auth.user} onLogout={async () => {
    try { await auth.logout(); } finally { router.replace("/"); }
  }} />;
}
