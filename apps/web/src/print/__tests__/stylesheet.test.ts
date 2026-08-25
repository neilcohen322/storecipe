import { PRINT_STYLESHEET } from "../stylesheet";

test("print stylesheet hides chrome and keeps recipe content", () => {
  expect(PRINT_STYLESHEET).toContain("@media print");
  expect(PRINT_STYLESHEET).toContain('[data-print-hide="true"] { display: none !important; }');
  expect(PRINT_STYLESHEET).toContain('[data-print-root="true"]');
  expect(PRINT_STYLESHEET).not.toContain("@keyframes");
});
