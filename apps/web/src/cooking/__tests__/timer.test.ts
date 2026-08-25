import {
  markElapsed,
  pauseTimer,
  remainingSeconds,
  replaceTimer,
  resumeTimer,
  startTimer,
} from "../timer";

const now = () => 1_000_000;

test("starts a running timer with a deadline", () => {
  const timer = startTimer({ durationSeconds: 90, instructionIndex: 2, now });
  expect(timer).toEqual({
    instructionIndex: 2,
    durationSeconds: 90,
    status: "running",
    deadlineMs: 1_090_000,
    remainingSeconds: null,
  });
  expect(remainingSeconds(timer, now)).toBe(90);
});

test("derives remaining time from deadline after refresh", () => {
  const timer = startTimer({ durationSeconds: 120, instructionIndex: 0, now: () => 1_000_000 });
  const refreshedNow = () => 1_050_000;
  expect(remainingSeconds(timer, refreshedNow)).toBe(70);
});

test("pauses and resumes using stored remaining seconds", () => {
  const running = startTimer({ durationSeconds: 60, instructionIndex: 0, now: () => 1_000_000 });
  const paused = pauseTimer(running, () => 1_020_000);
  expect(paused).toMatchObject({
    status: "paused",
    deadlineMs: null,
    remainingSeconds: 40,
  });
  expect(remainingSeconds(paused, () => 9_999_999)).toBe(40);

  const resumed = resumeTimer(paused, () => 1_030_000);
  expect(resumed).toMatchObject({
    status: "running",
    deadlineMs: 1_070_000,
    remainingSeconds: null,
  });
  expect(remainingSeconds(resumed, () => 1_040_000)).toBe(30);
});

test("marks elapsed timers at zero remaining seconds", () => {
  const running = startTimer({ durationSeconds: 30, instructionIndex: 0, now });
  const elapsed = markElapsed(running);
  expect(elapsed).toMatchObject({
    status: "elapsed",
    deadlineMs: null,
    remainingSeconds: 0,
  });
  expect(remainingSeconds(elapsed, now)).toBe(0);
});

test("replaceTimer starts a fresh timer", () => {
  const first = startTimer({ durationSeconds: 30, instructionIndex: 0, now });
  const second = replaceTimer({ durationSeconds: 45, instructionIndex: 3, now: () => 2_000_000 });
  expect(second).toEqual({
    instructionIndex: 3,
    durationSeconds: 45,
    status: "running",
    deadlineMs: 2_045_000,
    remainingSeconds: null,
  });
  expect(first.instructionIndex).toBe(0);
});
