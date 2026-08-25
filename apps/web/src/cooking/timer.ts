import type { CookingTimerState } from "./session";

export type CookingTimer = CookingTimerState;

export function remainingSeconds(
  timer: CookingTimer | null | undefined,
  now: () => number,
): number {
  if (!timer) {
    return 0;
  }
  if (timer.status === "elapsed") {
    return 0;
  }
  if (timer.status === "paused") {
    return timer.remainingSeconds ?? 0;
  }
  if (timer.status === "running" && timer.deadlineMs !== null) {
    return Math.max(0, Math.ceil((timer.deadlineMs - now()) / 1000));
  }
  return 0;
}

export function startTimer({
  durationSeconds,
  instructionIndex,
  now,
}: {
  durationSeconds: number;
  instructionIndex: number;
  now: () => number;
}): CookingTimer {
  return {
    instructionIndex,
    durationSeconds,
    status: "running",
    deadlineMs: now() + durationSeconds * 1000,
    remainingSeconds: null,
  };
}

export function pauseTimer(timer: CookingTimer, now: () => number): CookingTimer {
  return {
    ...timer,
    status: "paused",
    deadlineMs: null,
    remainingSeconds: remainingSeconds(timer, now),
  };
}

export function resumeTimer(timer: CookingTimer, now: () => number): CookingTimer {
  const remaining = timer.remainingSeconds ?? 0;
  return {
    ...timer,
    status: "running",
    deadlineMs: now() + remaining * 1000,
    remainingSeconds: null,
  };
}

export function markElapsed(timer: CookingTimer): CookingTimer {
  return {
    ...timer,
    status: "elapsed",
    deadlineMs: null,
    remainingSeconds: 0,
  };
}

export function replaceTimer({
  durationSeconds,
  instructionIndex,
  now,
}: {
  durationSeconds: number;
  instructionIndex: number;
  now: () => number;
}): CookingTimer {
  return startTimer({ durationSeconds, instructionIndex, now });
}
