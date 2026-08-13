type ErrorCategory = "api" | "auth" | "network";
const copy: Record<ErrorCategory, string> = {
  api: "We couldn't complete that request. Please try again.",
  auth: "Your session needs attention. Please sign in again.",
  network: "Check your connection and try again.",
};
export function presentError(error: unknown): string {
  if (typeof error === "object" && error !== null && "category" in error) {
    const category = (error as { category?: unknown }).category;
    if (category === "api" || category === "auth" || category === "network") return copy[category];
  }
  return copy.api;
}
