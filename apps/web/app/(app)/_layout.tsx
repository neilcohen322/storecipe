import { Slot } from "expo-router";

import { AppShell } from "../../src/components/AppShell";
import { AuthGate } from "../../src/auth/AuthGate";

export default function AuthenticatedLayout() {
  return <AuthGate><AppShell><Slot /></AppShell></AuthGate>;
}
