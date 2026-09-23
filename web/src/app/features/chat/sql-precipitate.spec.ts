import { describe, expect, it } from 'vitest';

import { buildSqlPrecipitateDraft } from './sql-precipitate';

describe('buildSqlPrecipitateDraft', () => {
  it('uses the user question as the sample name and keeps the prose as logic', () => {
    const markdown = [
      '按发电户识别分布式光伏。',
      '',
      '```sql',
      'SELECT 1',
      '```',
    ].join('\n');
    const draft = buildSqlPrecipitateDraft(markdown, 'SELECT 1', '查询分布式光伏用户');
    expect(draft.name).toBe('查询分布式光伏用户');
    expect(draft.logic).toBe('按发电户识别分布式光伏。');
    expect(draft.content).toBe('SELECT 1');
  });

  it('falls back to the first sentence when there is no question', () => {
    const draft = buildSqlPrecipitateDraft('先按地区筛选。再汇总。', 'SELECT 2', '  ');
    expect(draft.name).toBe('先按地区筛选');
    expect(draft.content).toBe('SELECT 2');
  });
});
