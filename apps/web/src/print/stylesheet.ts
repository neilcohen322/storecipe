export const PRINT_STYLESHEET = `html, body, #root { height: 100%; }
@media print {
  html, body, #root { height: auto !important; background: #ffffff !important; }
  [data-print-hide="true"] { display: none !important; }
  [data-print-root="true"] { position: static !important; overflow: visible !important; box-shadow: none !important; }
}`;
