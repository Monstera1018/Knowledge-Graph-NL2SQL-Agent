/** 对话页查询结果预览行数。 */
export const SQL_RESULT_PREVIEW_ROWS = 5;

export type SqlResultSet = {
  columns: string[];
  rows: Array<Record<string, string>>;
};

export function parseSqlResultSets(payload: Record<string, unknown>): SqlResultSet[] {
  const sets = payload['result_sets'];
  if (!Array.isArray(sets)) {
    return [];
  }
  return sets
    .map((item): SqlResultSet | null => {
      if (!item || typeof item !== 'object') {
        return null;
      }
      const columnsRaw = (item as Record<string, unknown>)['columns'];
      const rowsRaw = (item as Record<string, unknown>)['rows'];
      if (!Array.isArray(columnsRaw) || !Array.isArray(rowsRaw)) {
        return null;
      }
      const columns = columnsRaw.filter((col): col is string => typeof col === 'string');
      const rows = rowsRaw.filter((row): row is Record<string, string> => !!row && typeof row === 'object');
      return { columns, rows };
    })
    .filter((item): item is SqlResultSet => !!item);
}

export function previewSqlResultRows<T>(rows: readonly T[], limit = SQL_RESULT_PREVIEW_ROWS): T[] {
  return rows.slice(0, Math.max(0, limit));
}

export function sqlResultHasMore(rowCount: number, limit = SQL_RESULT_PREVIEW_ROWS): boolean {
  return rowCount > limit;
}
