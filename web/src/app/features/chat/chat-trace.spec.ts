import { describe, expect, it } from 'vitest';

import type { ChatAssistantMessage } from '../../core/models/chat.models';
import {
  classifySectionTier,
  defaultExpandedStepIds,
  parseTraceSteps,
  sectionMarkdown,
} from './chat-trace-parser';
import { findActiveExecutionTrace, isExecutionTracePart } from './chat-trace';

describe('chat-trace', () => {
  it('detects execution trace artifact parts', () => {
    expect(
      isExecutionTracePart({
        kind: 'artifact',
        id: 'trace-1',
        title: '执行追踪',
        language: 'json',
        content: '[]',
      }),
    ).toBe(true);
    expect(
      isExecutionTracePart({
        kind: 'artifact',
        id: 'sql-1',
        title: 'SQL',
        language: 'sql',
        content: 'SELECT 1',
      }),
    ).toBe(false);
  });

  it('finds trace from latest assistant message', () => {
    const assistant: ChatAssistantMessage = {
      id: 'a1',
      role: 'assistant',
      status: 'streaming',
      createdAt: '2026-01-01',
      parts: [
        {
          kind: 'artifact',
          id: 'trace-t1',
          title: '执行追踪',
          language: 'json',
          content: '[{"node":"generate_sql","label":"✍️ SQL 生成","sections":[{"title":"生成的 SQL","content":"SELECT 1","language":"sql"}]}]',
        },
      ],
    };
    expect(findActiveExecutionTrace([assistant])?.content).toContain('generate_sql');
  });
});

describe('chat-trace-parser', () => {
  it('parses structured json trace steps', () => {
    const steps = parseTraceSteps(
      JSON.stringify([
        {
          node: 'extract_retrieval',
          label: '📝 检索提取',
          sections: [{ title: 'System 提示词', content: 'router prompt', language: 'markdown' }],
        },
        {
          node: 'generate_sql',
          label: '✍️ SQL 生成',
          sections: [{ title: '生成的 SQL', content: 'SELECT 1', language: 'sql' }],
        },
      ]),
    );
    expect(steps).toHaveLength(2);
    expect(steps[0].node).toBe('extract_retrieval');
    expect(steps[0].status).toBe('done');
    expect(steps[0].debugSections).toHaveLength(1);
    expect(steps[0].summarySections).toHaveLength(0);
    expect(steps[1].detailSections).toHaveLength(1);
    expect(steps[1].emphasis).toBe('sql');
  });

  it('classifies summary and debug sections', () => {
    expect(classifySectionTier('召回依据')).toBe('summary');
    expect(classifySectionTier('System 提示词')).toBe('debug');
    expect(classifySectionTier('User 输入 (2)')).toBe('debug');
    expect(classifySectionTier('检索结果')).toBe('detail');
  });

  it('marks failed validation steps as error emphasis', () => {
    const steps = parseTraceSteps(
      JSON.stringify([
        {
          v: 1,
          node: 'validate_sql',
          label: '✅ SQL 合法性检查',
          status: 'error',
          sections: [{ title: '错误', content: 'SQL 合法性检查未通过', language: 'markdown' }],
        },
      ]),
    );
    expect(steps[0].status).toBe('error');
    expect(steps[0].emphasis).toBe('error');
    expect(steps[0].summarySections).toHaveLength(1);
  });

  it('wraps json trace sections in fenced code blocks', () => {
    const markdown = sectionMarkdown({
      title: 'QueryPlanIR',
      content: '{\n  "metric": "明细"\n}',
      language: 'json',
      tier: 'detail',
    });
    expect(markdown).toBe('```json\n{\n  "metric": "明细"\n}\n```');
  });

  it('expands only the active step while streaming', () => {
    const steps = parseTraceSteps(
      JSON.stringify([
        {
          node: 'semantic_search',
          label: '🔍 向量检索',
          sections: [{ title: '检索结果', content: 'body', language: 'markdown' }],
        },
        {
          node: 'generate_sql',
          label: '✍️ SQL 生成',
          sections: [{ title: '生成的 SQL', content: 'SELECT 1', language: 'sql' }],
        },
      ]),
    );
    const streamingExpanded = defaultExpandedStepIds(steps, true);
    expect(streamingExpanded.has(steps[0].id)).toBe(false);
    expect(streamingExpanded.has(steps[1].id)).toBe(true);

    const finishedExpanded = defaultExpandedStepIds(steps, false);
    expect(finishedExpanded.size).toBe(0);
  });
});
