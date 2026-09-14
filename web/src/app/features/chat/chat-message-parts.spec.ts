import { describe, expect, it } from 'vitest';

import type { DataPart, TextPart, ToolPart } from '../../core/models/chat.models';
import {
  isQueryResultText,
  mergeDisplayToolParts,
  partOrderKey,
  reorderQueryMarkdown,
  shouldShowArtifactPart,
  shouldShowDataPart,
  shouldShowTextPart,
  sortVisibleParts,
} from './chat-message-parts';

function textPart(id: string, text = 'result table'): TextPart {
  return { kind: 'text', id, text };
}

function queryResultTextPart(id: string): TextPart {
  return {
    kind: 'text',
    id,
    text: '## 📊 查询结果\n\n| a |\n| --- |\n| 1 |\n\n---\n\n## ✅ SQL 合法性检查\n\n已通过',
  };
}

function schemaPlanPart(): DataPart {
  return {
    kind: 'data',
    id: 'plan',
    schema: 'generic',
    title: '任务规划',
    truncated: false,
    payload: {
      kind: 'schema_plan',
      selected_tables: ['orders'],
      selected_columns: ['orders.status'],
    },
  };
}

function sqlDiagnosticPart(recoveryAction: string, retryCount = 0): DataPart {
  return {
    kind: 'data',
    id: 'diag',
    schema: 'generic',
    title: '查询未完成',
    truncated: false,
    payload: {
      kind: 'sql_diagnostic',
      recovery_action: recoveryAction,
      retry_count: retryCount,
      errors: ['Unknown column'],
    },
  };
}

describe('chat-message-parts', () => {
  it('orders thinking artifact before sql artifact', () => {
    const ordered = sortVisibleParts([
      {
        kind: 'artifact',
        id: 'sql',
        title: 'SQL',
        language: 'sql',
        content: 'SELECT 1',
      },
      {
        kind: 'artifact',
        id: 'thinking',
        title: '思考过程',
        language: 'thinking',
        content: '先确认字段',
      },
    ]);
    expect(ordered.map((part) => part.id)).toEqual(['thinking', 'sql']);
  });

  it('hides empty thinking artifact', () => {
    expect(
      shouldShowArtifactPart({
        kind: 'artifact',
        id: 'thinking',
        title: '思考过程',
        language: 'thinking',
        content: '   ',
      }),
    ).toBe(false);
  });

  it('orders plan before sql and query text after sql', () => {
    const ordered = sortVisibleParts([
      queryResultTextPart('result'),
      {
        kind: 'artifact',
        id: 'sql',
        title: 'SQL',
        language: 'sql',
        content: 'SELECT 1',
      },
      schemaPlanPart(),
    ]);
    expect(ordered.map((part) => part.id)).toEqual(['plan', 'sql', 'result']);
  });

  it('keeps plan before narrative text and sql', () => {
    const ordered = sortVisibleParts([
      queryResultTextPart('result'),
      textPart('intro', '这是分析说明'),
      schemaPlanPart(),
      {
        kind: 'artifact',
        id: 'sql',
        title: 'SQL',
        language: 'sql',
        content: 'SELECT 1',
      },
    ]);
    expect(ordered.map((part) => part.id)).toEqual(['plan', 'intro', 'sql', 'result']);
  });

  it('detects query result markdown text', () => {
    expect(isQueryResultText(queryResultTextPart('result'))).toBe(true);
    expect(isQueryResultText(textPart('plain', '普通回复'))).toBe(false);
  });

  it('puts validation before query results in markdown', () => {
    const reordered = reorderQueryMarkdown(queryResultTextPart('result').text);
    expect(reordered.indexOf('SQL 合法性检查')).toBeLessThan(reordered.indexOf('查询结果'));
  });

  it('shows sql diagnostics until they are resolved', () => {
    expect(shouldShowDataPart(sqlDiagnosticPart('repair', 1))).toBe(true);
    expect(shouldShowDataPart(sqlDiagnosticPart('stop', 3))).toBe(true);
    expect(
      shouldShowDataPart({
        ...sqlDiagnosticPart('repair', 1),
        payload: {
          ...sqlDiagnosticPart('repair', 1).payload,
          resolved: true,
        },
      }),
    ).toBe(false);
  });

  it('shows diagnostics while streaming', () => {
    expect(shouldShowDataPart(sqlDiagnosticPart('stop', 3), true)).toBe(true);
    expect(shouldShowDataPart(sqlDiagnosticPart('repair', 2), true)).toBe(true);
  });

  it('hides transient validation failures while streaming', () => {
    const failureText: TextPart = {
      kind: 'text',
      id: 'text-fail',
      text: '## ❌ SQL 合法性检查\n\n校验 **未通过**',
    };
    expect(shouldShowTextPart(failureText, true)).toBe(false);
    expect(shouldShowTextPart(failureText, false)).toBe(true);
  });

  it('hides resolved diagnostics after a successful retry', () => {
    expect(
      shouldShowDataPart({
        ...sqlDiagnosticPart('stop', 3),
        payload: {
          ...sqlDiagnosticPart('stop', 3).payload,
          resolved: true,
        },
      }),
    ).toBe(false);
  });

  it('prefers successful validation over earlier failures in markdown', () => {
    const reordered = reorderQueryMarkdown(
      [
        '## ❌ SQL 合法性检查',
        '',
        '校验 **未通过**',
        '---',
        '## ✅ SQL 合法性检查',
        '',
        '语法与表结构检查 **已通过**',
        '---',
        '## 📊 查询结果',
        '',
        '| a |',
      ].join('\n\n'),
    );
    expect(reordered).not.toContain('❌ SQL 合法性检查');
    expect(reordered).toContain('✅ SQL 合法性检查');
    expect(reordered).not.toMatch(/\n---\n/);
  });

  it('drops standalone horizontal rule chunks when reordering', () => {
    const reordered = reorderQueryMarkdown(
      '## ✅ SQL 合法性检查\n\nok\n\n---\n\n---\n\n## 📊 查询结果\n\n| a |',
    );
    expect(reordered.startsWith('---')).toBe(false);
    expect(reordered).toContain('SQL 合法性检查');
    expect(reordered).toContain('查询结果');
  });

  it('assigns query text after sql artifact', () => {
    expect(partOrderKey(schemaPlanPart())).toBeLessThan(
      partOrderKey({
        kind: 'artifact',
        id: 'sql',
        title: 'SQL',
        language: 'sql',
        content: 'SELECT 1',
      }),
    );
    expect(
      partOrderKey({
        kind: 'artifact',
        id: 'sql',
        title: 'SQL',
        language: 'sql',
        content: 'SELECT 1',
      }),
    ).toBeLessThan(partOrderKey(queryResultTextPart('result')));
  });

  it('merges planner and parse_plan into one task plan node', () => {
    const planner: ToolPart = {
      kind: 'tool',
      id: 't-planner',
      name: 'planner',
      phase: 'result',
      title: '规划步骤',
      input: null,
      output: { result: { steps: ['query'] } },
      duration_ms: 400,
    };
    const parsed: ToolPart = {
      kind: 'tool',
      id: 't-parse-plan',
      name: 'parse_plan',
      phase: 'result',
      title: '规划步骤',
      input: null,
      output: { result: { next: 'query', steps: ['query'], reason: '先查数' } },
      duration_ms: 200,
    };
    const parsedAgain: ToolPart = {
      kind: 'tool',
      id: 't-parse-plan-2',
      name: 'parse_plan',
      phase: 'result',
      title: '规划步骤',
      input: null,
      output: {
        result: {
          next: 'chart',
          steps: ['query', 'chart'],
          reason: '查询成功后再出图',
          decisions: [
            { next: 'query', reason: '先查数' },
            { next: 'chart', reason: '查询成功后再出图' },
          ],
        },
      },
      duration_ms: 300,
    };
    const merged = mergeDisplayToolParts([planner, parsed, parsedAgain]);
    expect(merged).toHaveLength(1);
    expect(merged[0].kind).toBe('tool');
    if (merged[0].kind !== 'tool') {
      return;
    }
    expect(merged[0].name).toBe('task_plan');
    expect(merged[0].duration_ms).toBe(900);
    const output = merged[0].output as Record<string, unknown>;
    expect(output['result']).toEqual({
      next: 'chart',
      steps: ['query', 'chart'],
      reason: '查询成功后再出图',
      decisions: [
        { next: 'query', reason: '先查数' },
        { next: 'chart', reason: '查询成功后再出图' },
      ],
    });
  });

  it('merges report planner tools into one analysis report node', () => {
    const prepare: ToolPart = {
      kind: 'tool',
      id: 't-prepare-report',
      name: 'prepare_report_planner_input',
      phase: 'result',
      title: '分析报告',
      input: null,
      output: { stage: 'prepare_report_planner_input', status: 'ok' },
      duration_ms: 80,
    };
    const planner: ToolPart = {
      kind: 'tool',
      id: 't-report-planner',
      name: 'report_planner',
      phase: 'result',
      title: '分析报告',
      input: null,
      output: { stage: 'report_planner', status: 'ok', summary: '按区县分析' },
      duration_ms: 900,
    };
    const parsed: ToolPart = {
      kind: 'tool',
      id: 't-parse-report',
      name: 'parse_report_spec',
      phase: 'result',
      title: '分析报告',
      input: null,
      output: {
        stage: 'parse_report_spec',
        status: 'ok',
        result: { kind: 'analysis_report', feasible: true },
        summary: '已生成分析报告',
      },
      duration_ms: 400,
    };
    const merged = mergeDisplayToolParts([prepare, planner, parsed]);
    expect(merged).toHaveLength(1);
    expect(merged[0].kind).toBe('tool');
    if (merged[0].kind !== 'tool') {
      return;
    }
    expect(merged[0].name).toBe('report_analysis');
    expect(merged[0].title).toBe('分析报告');
    expect(merged[0].duration_ms).toBe(1380);
  });
});
