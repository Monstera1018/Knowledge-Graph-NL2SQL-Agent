import { describe, expect, it } from 'vitest';

import { ANTD_PRIMARY, buildChartOption } from './chat-chart-option';
import type { ChatChartView } from './chat-chart';

function view(type: ChatChartView['type']): ChatChartView {
  return {
    title: '示例',
    type,
    note: '',
    categories: ['杭州', '宁波', '金华'],
    series: [{ name: '数量', values: [3, 2, 1] }],
    palette: [],
  };
}

function firstSeries(option: ReturnType<typeof buildChartOption>): Record<string, unknown> {
  const series = option['series'] as Array<Record<string, unknown>>;
  return series[0];
}

describe('buildChartOption', () => {
  it('styles line charts as smooth area with markers', () => {
    const option = buildChartOption(view('line'));
    const series = firstSeries(option);
    const colors = option['color'] as string[];
    expect(series['type']).toBe('line');
    expect(series['smooth']).toBeTruthy();
    expect(series['showSymbol']).toBe(true);
    expect(series['areaStyle']).toBeTruthy();
    expect(series['markPoint']).toBeTruthy();
    expect(colors[0]).toBe(ANTD_PRIMARY);
  });

  it('adds rounded bars, top labels, and an average mark', () => {
    const option = buildChartOption(view('bar'));
    const series = firstSeries(option);
    const data = series['data'] as Array<Record<string, unknown>>;
    const itemStyle = data[0]['itemStyle'] as Record<string, unknown>;
    const label = series['label'] as Record<string, unknown>;
    expect(series['type']).toBe('bar');
    expect(itemStyle['borderRadius']).toEqual([8, 8, 0, 0]);
    expect(label['show']).toBe(true);
    expect(label['position']).toBe('top');
    expect(series['markLine']).toBeTruthy();
    expect(option['animation']).toBe(true);
    expect((data[0]['itemStyle'] as Record<string, unknown>)['shadowBlur']).toBe(10);
  });

  it('renders pie as a labeled donut with a center total', () => {
    const option = buildChartOption(view('pie'));
    const series = firstSeries(option);
    const seriesList = option['series'] as Array<Record<string, unknown>>;
    const centerLabel = (seriesList[1]['label'] as Record<string, unknown>);
    expect(series['radius']).toEqual(['48%', '70%']);
    expect((series['itemStyle'] as Record<string, unknown>)['borderRadius']).toBe(6);
    expect((series['labelLine'] as Record<string, unknown>)['length']).toBe(12);
    expect(option['legend']).toBeTruthy();
    expect(seriesList[1]['center']).toEqual(series['center']);
    expect(centerLabel['position']).toBe('center');
    expect(centerLabel['formatter']).toContain('6');
    expect(centerLabel['formatter']).toContain('总计');
    expect(series['animationType']).toBe('expansion');
  });

  it('fills radar with translucent blue', () => {
    const series = firstSeries(buildChartOption(view('radar')));
    const data = series['data'] as Array<Record<string, unknown>>;
    expect(series['type']).toBe('radar');
    expect((data[0]['areaStyle'] as Record<string, unknown>)['color']).toContain('0.28');
  });

  it('renders funnel with inside labels and rounded layers', () => {
    const series = firstSeries(buildChartOption(view('funnel')));
    expect(series['type']).toBe('funnel');
    expect(series['sort']).toBe('descending');
    expect((series['itemStyle'] as Record<string, unknown>)['borderRadius']).toBe(8);
    expect((series['label'] as Record<string, unknown>)['position']).toBe('inside');
  });
});
