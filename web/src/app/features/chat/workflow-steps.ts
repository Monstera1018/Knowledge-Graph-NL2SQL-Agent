import type { WorkflowStatus } from '../../core/models/chat.models';

export interface ParsedWorkflowStep {
  icon: string | null;
  label: string;
  status: 'active' | 'done';
  subSteps?: ParsedWorkflowStep[];
}

const STEP_LINE = /^([→✓✅])\s+(.*)$/u;
const EMOJI_PREFIX = /^((?:\p{Extended_Pictographic}\uFE0F?\u200D?)+)\s*/u;

function splitIconAndLabel(raw: string): { icon: string | null; label: string } {
  const match = raw.match(EMOJI_PREFIX);
  if (!match) {
    return { icon: null, label: raw.trim() };
  }
  return {
    icon: match[1].trim(),
    label: raw.slice(match[0].length).trim(),
  };
}

function parseStepLine(raw: string): { marker: string; icon: string | null; label: string } {
  const match = raw.trim().match(STEP_LINE);
  const marker = match?.[1] ?? '→';
  const { icon, label } = splitIconAndLabel((match?.[2] ?? raw).trim());
  return { marker, icon, label };
}

function markActiveSteps(steps: ParsedWorkflowStep[], workflowStatus: WorkflowStatus): void {
  if (workflowStatus !== 'running' || steps.length === 0) {
    return;
  }

  const lastMain = steps[steps.length - 1];
  const subs = lastMain.subSteps ?? [];
  const lastSub = subs[subs.length - 1];

  if (lastSub) {
    lastSub.status = 'active';
    lastMain.status = 'active';
    return;
  }

  lastMain.status = 'active';
}

export function parseWorkflowSteps(
  detail: string,
  workflowStatus: WorkflowStatus,
): ParsedWorkflowStep[] {
  const steps: ParsedWorkflowStep[] = [];

  for (const rawLine of (detail ?? '').split('\n')) {
    if (!rawLine.trim()) {
      continue;
    }

    if (rawLine.startsWith('  ')) {
      const parsed = parseStepLine(rawLine);
      const parent = steps[steps.length - 1];
      if (!parent) {
        continue;
      }
      parent.subSteps ??= [];
      parent.subSteps.push({
        icon: parsed.icon,
        label: parsed.label,
        status: 'done',
      });
      continue;
    }

    const parsed = parseStepLine(rawLine);
    steps.push({
      icon: parsed.icon,
      label: parsed.label,
      status: 'done',
    });
  }

  markActiveSteps(steps, workflowStatus);
  return steps;
}

export function resolveActiveWorkflowStep(
  steps: ParsedWorkflowStep[],
  workflowStatus: WorkflowStatus,
): ParsedWorkflowStep | null {
  if (workflowStatus !== 'running') {
    return null;
  }

  const activeMain = steps.find((step) => step.status === 'active');
  if (!activeMain) {
    return steps.at(-1) ?? null;
  }

  const activeSub = activeMain.subSteps?.find((step) => step.status === 'active');
  return activeSub ?? activeMain;
}

export function countWorkflowItems(steps: ParsedWorkflowStep[]): number {
  return steps.reduce(
    (total, step) => total + 1 + (step.subSteps?.length ?? 0),
    0,
  );
}
