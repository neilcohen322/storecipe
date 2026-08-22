import { resolveApiBases } from "../bases";

test("keeps separate local development defaults", () => {
  expect(resolveApiBases()).toEqual({
    catalog: "http://localhost:8000",
    ingestion: "http://localhost:8001",
  });
});

test("uses one validated HTTPS origin in production", () => {
  expect(resolveApiBases("https://storecipe.example/", "https://storecipe.example")).toEqual({
    catalog: "https://storecipe.example",
    ingestion: "https://storecipe.example",
  });
});

test("rejects split production origins", () => {
  expect(() =>
    resolveApiBases("https://api.storecipe.example", "https://imports.storecipe.example"),
  ).toThrow("one HTTPS origin");
});
