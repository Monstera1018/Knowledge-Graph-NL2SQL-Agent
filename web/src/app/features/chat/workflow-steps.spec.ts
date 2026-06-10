import { describe, expect, it } from 'vitest';

import {
  countWorkflowItems,
  parseWorkflowSteps,
  resolveActiveWorkflowStep,
} from './workflow-steps';

describe('parseWorkflowSteps', () => {
  it('marks the last arrow line as active while running', () => {
    const detail = '✓ 🧭 正在识别对话意图…\n→ 🔍 正在检索相关业务知识与表结构…';
    const steps = parseWorkflowSteps(detail, 'running');
    expect(steps).toHaveLength(2);
    expect(steps[0].status).toBe('done');
    expect(steps[1].status).toBe('active');
  });

  it('parses sql candidate comparison sub-steps', () => {
    const detail =
      '→ ✍️ 正在生成 SQL…\n  ✓ ✍️ 正在比对 SQL 候选…\n  → ✍️ 正在生成保守版 SQL 候选…';
    const steps = parseWorkflowSteps(detail, 'running');
    expect(steps).toHaveLength(1);
    expect(steps[0].subSteps).toHaveLength(2);
    expect(steps[0].subSteps?.[0].status).toBe('done');
    expect(steps[0].subSteps?.[1].status).toBe('active');
    expect(resolveActiveWorkflowStep(steps, 'running')?.label).toBe(
      '正在生成保守版 SQL 候选…',
    );
  });

  it('parses sql generation sub-steps', () => {
    const detail =
      '→ ✍️ 正在生成 SQL…\n  → 🔎 模型探查中（第 1 轮）…';
    const steps = parseWorkflowSteps(detail, 'running');
    expect(steps).toHaveLength(1);
    expect(steps[0].label).toBe('正在生成 SQL…');
    expect(steps[0].subSteps).toHaveLength(1);
    expect(steps[0].subSteps?.[0].status).toBe('active');
    expect(resolveActiveWorkflowStep(steps, 'running')?.label).toBe(
      '模型探查中（第 1 轮）…',
    );
  });

  it('marks every step done when workflow finished', () => {
    const detail = '✓ 🧭 正在识别对话意图…\n✓ 📊 正在执行查询…';
    const steps = parseWorkflowSteps(detail, 'done');
    expect(steps.every((step) => step.status === 'done')).toBe(true);
  });

  it('counts nested workflow items', () => {
    const detail =
      '✓ 🧭 step\n→ ✍️ sql\n  → 🔎 probe';
    const steps = parseWorkflowSteps(detail, 'running');
    expect(countWorkflowItems(steps)).toBe(3);
  });
});
