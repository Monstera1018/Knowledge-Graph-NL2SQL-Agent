import { describe, expect, it } from 'vitest';

import {
  SQL_RESULT_PREVIEW_ROWS,
  parseSqlResultSets,
  previewSqlResultRows,
  sqlResultHasMore,
} from './chat-sql-result';

describe('chat-sql-result', () => {
  it('parses result sets and ignores malformed entries', () => {
    const sets = parseSqlResultSets({
      result_sets: [
        { columns: ['用户编号', '地区'], rows: [{ 用户编号: '1', 地区: '金华' }] },
        { columns: 'bad', rows: [] },
        null,
      ],
    });
    expect(sets).toEqual([
      { columns: ['用户编号', '地区'], rows: [{ 用户编号: '1', 地区: '金华' }] },
    ]);
  });

  it('previews the first five rows and reports overflow', () => {
    const rows = Array.from({ length: 8 }, (_, index) => ({ id: String(index + 1) }));
    expect(previewSqlResultRows(rows)).toHaveLength(SQL_RESULT_PREVIEW_ROWS);
    expect(previewSqlResultRows(rows).map((row) => row.id)).toEqual(['1', '2', '3', '4', '5']);
    expect(sqlResultHasMore(rows.length)).toBe(true);
    expect(sqlResultHasMore(5)).toBe(false);
    expect(sqlResultHasMore(0)).toBe(false);
  });
});
