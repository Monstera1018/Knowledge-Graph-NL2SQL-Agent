import { describe, expect, it } from 'vitest';

import type { DataPart, TextPart } from '../../core/models/chat.models';
import {
  isQueryResultText,
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
});
