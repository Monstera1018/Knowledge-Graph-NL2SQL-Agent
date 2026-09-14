import { describe, expect, it } from 'vitest';

import { parseChartSpec } from './chat-chart';

describe('parseChartSpec', () => {
  it('keeps valid bar charts and drops incomplete ones', () => {
    const spec = parseChartSpec({
      feasible: true,
      summary: '按地区统计',
      source_row_count: 3,
      charts: [
        {
          title: '各地区用户数',
          type: 'bar',
          note: '基于当前 3 行',
          categories: ['金华', '宁波'],
          series: [{ name: '用户数', values: [2, 1] }],
        },
        { title: '无效', type: 'radar', categories: ['a'], series: [] },
      ],
    });
    expect(spec.feasible).toBe(true);
    expect(spec.charts).toHaveLength(1);
    expect(spec.charts[0].type).toBe('bar');
    expect(spec.charts[0].series[0].values).toEqual([2, 1]);
    expect(spec.charts[0].palette).toEqual([]);
  });

  it('keeps chart palettes from payload', () => {
    const spec = parseChartSpec({
      feasible: true,
      summary: '橙黄饼图',
      source_row_count: 2,
      palette: ['#ea580c', '#f59e0b'],
      charts: [
        {
          title: '各地区用户数',
          type: 'pie',
          note: '',
          categories: ['金华', '宁波'],
          series: [{ name: '用户数', values: [2, 1] }],
          palette: ['#ea580c', '#f59e0b'],
        },
      ],
    });
    expect(spec.charts[0].palette).toEqual(['#ea580c', '#f59e0b']);
  });

  it('marks infeasible plans without charts', () => {
    const spec = parseChartSpec({
      feasible: false,
      summary: '请先问数',
      charts: [],
    });
    expect(spec.feasible).toBe(false);
    expect(spec.charts).toEqual([]);
  });
});
