import { ScrollViewStyleReset } from "expo-router/html";
import type { PropsWithChildren } from "react";

import { PRINT_STYLESHEET } from "../src/print/stylesheet";

export default function Html({ children }: PropsWithChildren) {
  return (
    <html lang="en" style={{ height: "100%" }}>
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
        <ScrollViewStyleReset />
        <style dangerouslySetInnerHTML={{ __html: PRINT_STYLESHEET }} />
      </head>
      <body style={{ height: "100%" }}>{children}</body>
    </html>
  );
}
