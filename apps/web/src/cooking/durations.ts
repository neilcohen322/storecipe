type ParsedDuration = { totalSeconds: number; label: string };

const RANGE_PATTERNS = [
  /\d+\s*[-–]\s*\d+\s*(?:hours?|hrs?|minutes?|mins?)\b/gi,
  /\d+\s+to\s+\d+\s*(?:hours?|hrs?|minutes?|mins?)\b/gi,
] as const;

const VAGUE_PATTERNS = [
  /\b(?:a few|several)\s+(?:minutes?|mins?|hours?|hrs?)\b/gi,
  /\buntil\s+\w+/gi,
  /\babout\s+\d+\s*(?:minutes?|mins?|hours?|hrs?)\b/gi,
] as const;

const COMBINED_PATTERN =
  /(\d+)\s*(?:hours?|hrs?)\s+(\d+)\s*(?:minutes?|mins?)\b/gi;
const HOUR_PATTERN = /(\d+)\s*(?:hours?|hrs?)\b/gi;
const MINUTE_PATTERN = /(\d+)\s*(?:minutes?|mins?)\b/gi;

function collectExcludedSpans(text: string): Array<[number, number]> {
  const spans: Array<[number, number]> = [];
  for (const pattern of [...RANGE_PATTERNS, ...VAGUE_PATTERNS]) {
    pattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(text)) !== null) {
      spans.push([match.index, match.index + match[0].length]);
    }
  }
  return spans;
}

function overlapsExcluded(
  start: number,
  end: number,
  excluded: Array<[number, number]>,
): boolean {
  return excluded.some(([spanStart, spanEnd]) => start < spanEnd && end > spanStart);
}

function formatLabel(hours: number, minutes: number): string {
  const parts: string[] = [];
  if (hours > 0) {
    parts.push(hours === 1 ? "1 hour" : `${hours} hours`);
  }
  if (minutes > 0) {
    parts.push(minutes === 1 ? "1 minute" : `${minutes} minutes`);
  }
  return parts.join(" ");
}

function toDuration(hours: number, minutes: number): ParsedDuration {
  return {
    totalSeconds: hours * 3600 + minutes * 60,
    label: formatLabel(hours, minutes),
  };
}

export function parseInstructionDuration(instruction: string): ParsedDuration | null {
  const excluded = collectExcludedSpans(instruction);

  COMBINED_PATTERN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = COMBINED_PATTERN.exec(instruction)) !== null) {
    const start = match.index;
    const end = start + match[0].length;
    if (!overlapsExcluded(start, end, excluded)) {
      return toDuration(parseInt(match[1], 10), parseInt(match[2], 10));
    }
  }

  HOUR_PATTERN.lastIndex = 0;
  while ((match = HOUR_PATTERN.exec(instruction)) !== null) {
    const start = match.index;
    const end = start + match[0].length;
    if (!overlapsExcluded(start, end, excluded)) {
      return toDuration(parseInt(match[1], 10), 0);
    }
  }

  MINUTE_PATTERN.lastIndex = 0;
  while ((match = MINUTE_PATTERN.exec(instruction)) !== null) {
    const start = match.index;
    const end = start + match[0].length;
    if (!overlapsExcluded(start, end, excluded)) {
      return toDuration(0, parseInt(match[1], 10));
    }
  }

  return null;
}
