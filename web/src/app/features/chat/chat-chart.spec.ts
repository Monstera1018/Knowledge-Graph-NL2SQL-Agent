import { describe, expect, it } from 'vitest';

import { formatChartNumber, parseChartSpec, summarizeChartKpis } from './chat-chart';

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
        { title: '无效', type: 'unknown', categories: ['a'], series: [] },
      ],
    });
    expect(spec.feasible).toBe(true);
    expect(spec.charts).toHaveLength(1);
    expect(spec.charts[0].type).toBe('bar');
    expect(spec.charts[0].series[0].values).toEqual([2, 1]);
    expect(spec.charts[0].palette).toEqual([]);
    expect(spec.sourceTruncated).toBe(false);
  });

  it('keeps truncated snapshot flag', () => {
    const spec = parseChartSpec({
      feasible: true,
      summary: '按地区统计',
      source_row_count: 100,
      source_truncated: true,
      charts: [
        {
          title: '各地区用户数',
          type: 'bar',
          categories: ['金华'],
          series: [{ name: '用户数', values: [2] }],
        },
      ],
    });
    expect(spec.sourceTruncated).toBe(true);
    expect(spec.sourceRowCount).toBe(100);
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

  it('keeps radar and scatter charts', () => {
    const spec = parseChartSpec({
      feasible: true,
      summary: '多图',
      source_row_count: 4,
      charts: [
        {
          title: '地区轮廓',
          type: 'radar',
          categories: ['杭州', '宁波', '金华'],
          series: [{ name: '工单数', values: [3, 2, 1] }],
        },
        {
          title: '容量与电量',
          type: 'scatter',
          categories: ['10', '20', '30'],
          series: [{ name: '电量', values: [100, 80, 40] }],
        },
      ],
    });
    expect(spec.charts.map((item) => item.type)).toEqual(['radar', 'scatter']);
  });

  it('summarizes KPI totals and the largest category', () => {
    const kpis = summarizeChartKpis({
      title: '各地区用户数',
      type: 'bar',
      note: '',
      categories: ['金华', '宁波'],
      series: [{ name: '用户数', values: [2, 1] }],
      palette: [],
    });
    expect(kpis.map((item) => item.label)).toEqual(['合计', '最大类', '最大占比', '均值']);
    expect(kpis[0].value).toBe('3');
    expect(kpis[1].value).toBe('金华');
    expect(kpis[2].value).toBe('66.7%');
    expect(formatChartNumber(12000)).toBe('1.2万');
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
