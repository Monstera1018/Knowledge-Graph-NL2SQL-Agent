export const CHAT_CHART_TYPES = [
  'bar',
  'line',
  'area',
  'hbar',
  'pie',
  'radar',
  'scatter',
  'funnel',
] as const;

export type ChatChartType = (typeof CHAT_CHART_TYPES)[number];

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
  sourceTruncated: boolean;
  palette: string[];
  charts: ChatChartView[];
}

export const DEFAULT_CHART_PALETTE = ['#1677ff', '#69b1ff', '#36cfc9', '#faad14', '#722ed1'];

export interface ChartKpiItem {
  label: string;
  value: string;
  hint?: string;
}

export function formatChartNumber(value: number): string {
  if (!Number.isFinite(value)) {
    return '—';
  }
  const abs = Math.abs(value);
  if (abs >= 10000) {
    const wan = value / 10000;
    return `${wan.toFixed(abs >= 100000 ? 0 : 1)}万`;
  }
  return value.toLocaleString('zh-CN', {
    maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
  });
}

export function summarizeChartKpis(chart: ChatChartView): ChartKpiItem[] {
  const values = chart.series[0]?.values ?? [];
  const pairs = chart.categories.map((name, index) => ({
    name,
    value: values[index] ?? 0,
  }));
  if (!pairs.length) {
    return [];
  }
  const total = pairs.reduce((sum, item) => sum + item.value, 0);
  const max = pairs.reduce((best, item) => (item.value > best.value ? item : best), pairs[0]);
  const share = total > 0 ? (max.value / total) * 100 : 0;
  const average = total / pairs.length;
  const metric = chart.series[0]?.name || '数值';
  const items: ChartKpiItem[] = [
    { label: '合计', value: formatChartNumber(total), hint: metric },
    { label: '最大类', value: max.name, hint: formatChartNumber(max.value) },
    { label: '最大占比', value: `${share.toFixed(1)}%` },
    { label: '均值', value: formatChartNumber(average) },
  ];
  if (chart.type === 'funnel' && pairs.length >= 2 && pairs[0].value > 0) {
    const last = pairs[pairs.length - 1];
    items[3] = {
      label: '转化率',
      value: `${((last.value / pairs[0].value) * 100).toFixed(1)}%`,
      hint: `${pairs[0].name} → ${last.name}`,
    };
  }
  return items;
}

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

function isChatChartType(value: string): value is ChatChartType {
  return (CHAT_CHART_TYPES as readonly string[]).includes(value);
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
      if (!isChatChartType(type)) {
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
    sourceTruncated: payload['source_truncated'] === true,
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
