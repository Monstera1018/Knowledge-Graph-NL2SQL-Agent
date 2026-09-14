import type { ToolPart } from '../../core/models/chat.models';

export function toolPartDurationMs(part: ToolPart): number | undefined {
  if (typeof part.duration_ms === 'number' && Number.isFinite(part.duration_ms)) {
    return Math.max(0, Math.round(part.duration_ms));
  }
  if (!part.started_at || !part.ended_at) {
    return undefined;
  }
  const started = Date.parse(part.started_at);
  const ended = Date.parse(part.ended_at);
  if (!Number.isFinite(started) || !Number.isFinite(ended) || ended < started) {
    return undefined;
  }
  return ended - started;
}

export function mergeToolTiming(
  parts: readonly ToolPart[],
): Pick<ToolPart, 'started_at' | 'ended_at' | 'duration_ms'> {
  const running = [...parts].reverse().find((part) => part.phase === 'call');
  if (running) {
    return running.started_at ? { started_at: running.started_at } : {};
  }

  const startedAt = parts.map((part) => part.started_at).find((value) => Boolean(value));
  const endedAt = [...parts].reverse().map((part) => part.ended_at).find((value) => Boolean(value));
  const knownDurations = parts
    .map((part) => toolPartDurationMs(part))
    .filter((value): value is number => value != null);

  return {
    ...(startedAt ? { started_at: startedAt } : {}),
    ...(endedAt ? { ended_at: endedAt } : {}),
    ...(knownDurations.length ? { duration_ms: knownDurations.reduce((sum, item) => sum + item, 0) } : {}),
  };
}

export function formatToolDurationMs(ms: number): string {
  const safe = Math.max(0, Math.round(ms));
  if (safe < 10_000) {
    return `${(safe / 1000).toFixed(1)} 秒`;
  }
  const totalSeconds = Math.round(safe / 1000);
  if (totalSeconds < 60) {
    return `${totalSeconds} 秒`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes} 分 ${String(seconds).padStart(2, '0')} 秒`;
}
