export type ChatChartType = 'bar' | 'line' | 'pie';

export interface ChatChartSeries {
  name: string;
  values: number[];
}

export interface ChatChartView {
  title: string;
  type: ChatChartType;
  note: string;
  categories: string[];
  series: ChatChartSeries[];
  palette: string[];
}

export interface ChatChartSpec {
  feasible: boolean;
  summary: string;
  sourceRowCount: number;
  palette: string[];
  charts: ChatChartView[];
}

export const DEFAULT_CHART_PALETTE = ['#2563eb', '#0ea5e9', '#059669', '#d97706', '#7c3aed'];

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value.replace(/,/g, ''));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function asPalette(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === 'string' && /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(item.trim()));
}

export function parseChartSpec(payload: Record<string, unknown>): ChatChartSpec {
  const summary = asString(payload['summary']);
  const sourceRowCount =
    typeof payload['source_row_count'] === 'number' ? payload['source_row_count'] : 0;
  const specPalette = asPalette(payload['palette']);
  const rawCharts = payload['charts'];
  const charts: ChatChartView[] = [];
  if (Array.isArray(rawCharts)) {
    for (const item of rawCharts) {
      if (!item || typeof item !== 'object') {
        continue;
      }
      const record = item as Record<string, unknown>;
      const type = asString(record['type']);
      if (type !== 'bar' && type !== 'line' && type !== 'pie') {
        continue;
      }
      const categories = Array.isArray(record['categories'])
        ? record['categories'].filter((value): value is string => typeof value === 'string')
        : [];
      const series: ChatChartSeries[] = [];
      const rawSeries = record['series'];
      if (Array.isArray(rawSeries)) {
        for (const entry of rawSeries) {
          if (!entry || typeof entry !== 'object') {
            continue;
          }
          const seriesRecord = entry as Record<string, unknown>;
          const valuesRaw = seriesRecord['values'];
          if (!Array.isArray(valuesRaw)) {
            continue;
          }
          const values = valuesRaw.map((value) => asNumber(value) ?? 0);
          series.push({
            name: asString(seriesRecord['name']) || '数值',
            values,
          });
        }
      }
      if (!categories.length || !series.length) {
        continue;
      }
      const chartPalette = asPalette(record['palette']);
      charts.push({
        title: asString(record['title']) || '查询结果',
        type,
        note: asString(record['note']),
        categories,
        series,
        palette: chartPalette.length ? chartPalette : specPalette,
      });
    }
  }
  return {
    feasible: payload['feasible'] === true && charts.length > 0,
    summary,
    sourceRowCount,
    palette: specPalette,
    charts,
  };
}

export function parseChartView(raw: unknown): ChatChartView | null {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  return parseChartSpec({ charts: [raw], feasible: true, summary: '' }).charts[0] ?? null;
}
