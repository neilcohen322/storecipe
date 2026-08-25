import { parseInstructionDuration } from "../durations";

test("parses explicit minute durations", () => {
  expect(parseInstructionDuration("Bake for 12 minutes")).toEqual({
    totalSeconds: 12 * 60,
    label: "12 minutes",
  });
  expect(parseInstructionDuration("Rest for 1 minute")).toEqual({
    totalSeconds: 60,
    label: "1 minute",
  });
  expect(parseInstructionDuration("Simmer 12 min")).toEqual({
    totalSeconds: 12 * 60,
    label: "12 minutes",
  });
  expect(parseInstructionDuration("Simmer 12 mins")).toEqual({
    totalSeconds: 12 * 60,
    label: "12 minutes",
  });
});

test("parses explicit hour durations", () => {
  expect(parseInstructionDuration("Roast for 1 hour")).toEqual({
    totalSeconds: 3600,
    label: "1 hour",
  });
  expect(parseInstructionDuration("Roast for 2 hours")).toEqual({
    totalSeconds: 7200,
    label: "2 hours",
  });
  expect(parseInstructionDuration("Roast for 1 hr")).toEqual({
    totalSeconds: 3600,
    label: "1 hour",
  });
  expect(parseInstructionDuration("Roast for 2 hrs")).toEqual({
    totalSeconds: 7200,
    label: "2 hours",
  });
});

test("parses combined hour and minute durations as one value", () => {
  expect(parseInstructionDuration("Bake 1 hour 15 minutes")).toEqual({
    totalSeconds: 75 * 60,
    label: "1 hour 15 minutes",
  });
});

test("ignores ranges and vague durations", () => {
  expect(parseInstructionDuration("Bake 10-12 minutes")).toBeNull();
  expect(parseInstructionDuration("Bake 10 to 12 minutes")).toBeNull();
  expect(parseInstructionDuration("Bake 10–12 min")).toBeNull();
  expect(parseInstructionDuration("Cook for a few minutes")).toBeNull();
  expect(parseInstructionDuration("Cook for several minutes")).toBeNull();
  expect(parseInstructionDuration("Cook until golden")).toBeNull();
  expect(parseInstructionDuration("Cook for about 10 minutes")).toBeNull();
});

test("returns only the first explicit single duration", () => {
  expect(parseInstructionDuration("Bake 10 minutes then cool 5 minutes")).toEqual({
    totalSeconds: 10 * 60,
    label: "10 minutes",
  });
});
