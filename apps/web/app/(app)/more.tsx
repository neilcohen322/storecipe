import { useRouter } from "expo-router";

import { useAuth } from "../../src/auth/AuthProvider";
import { MoreScreen } from "../../src/screens/MoreScreen";

export default function MoreRoute() {
  const auth = useAuth();
  const router = useRouter();
  return <MoreScreen onNavigate={(href) => router.push(href)} onLogout={async () => {
    try { await auth.logout(); } finally { router.replace("/"); }
  }} />;
}
