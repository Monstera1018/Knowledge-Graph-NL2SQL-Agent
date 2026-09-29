import * as echarts from 'echarts/core';
import type { EChartsCoreOption } from 'echarts/core';

import type { ChatChartView } from './chat-chart';
import { DEFAULT_CHART_PALETTE, formatChartNumber } from './chat-chart';

export const ANTD_PRIMARY = '#1677ff';

const AXIS_LINE = '#f0f0f0';
const AXIS_TEXT = '#8c8c8c';
const TITLE_TEXT = '#262626';
const TOOLTIP_SHADOW = '0 6px 16px rgb(0 0 0 / 0.08)';

function hexToRgba(hex: string, alpha: number): string {
  const raw = hex.replace('#', '');
  const normalized = raw.length === 3 ? raw.split('').map((part) => part + part).join('') : raw;
  const value = Number.parseInt(normalized, 16);
  if (!Number.isFinite(value)) {
    return `rgba(22, 119, 255, ${alpha})`;
  }
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function resolvePalette(spec: ChatChartView): string[] {
  return spec.palette.length ? spec.palette : DEFAULT_CHART_PALETTE;
}

function primaryColor(palette: string[]): string {
  return palette[0] || ANTD_PRIMARY;
}

function commonTextStyle() {
  return {
    color: AXIS_TEXT,
    fontSize: 11,
  };
}

function cardTooltip(trigger: 'item' | 'axis') {
  return {
    trigger,
    backgroundColor: '#fff',
    borderColor: '#f0f0f0',
    borderWidth: 1,
    textStyle: { color: TITLE_TEXT, fontSize: 12 },
    extraCssText: `box-shadow:${TOOLTIP_SHADOW};border-radius:8px;padding:8px 12px;`,
  };
}

function valueAxis() {
  return {
    type: 'value' as const,
    splitLine: { lineStyle: { color: AXIS_LINE, type: 'dashed' as const } },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: commonTextStyle(),
  };
}

function categoryAxis(data: string[]) {
  return {
    type: 'category' as const,
    data,
    axisTick: { show: false },
    axisLine: { lineStyle: { color: AXIS_LINE } },
    axisLabel: { ...commonTextStyle(), hideOverlap: true },
  };
}

function verticalBarGradient(color: string) {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: hexToRgba(color, 0.92) },
    { offset: 1, color },
  ]);
}

function horizontalBarGradient(color: string) {
  return new echarts.graphic.LinearGradient(0, 0, 1, 0, [
    { offset: 0, color },
    { offset: 1, color: hexToRgba(color, 0.75) },
  ]);
}

function areaGradient(color: string) {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: hexToRgba(color, 0.38) },
    { offset: 1, color: hexToRgba(color, 0.02) },
  ]);
}

function maxValueIndex(values: number[]): number {
  return values.reduce((best, value, index, list) => (value > list[best] ? index : best), 0);
}

function seriesTotal(values: number[]): number {
  return values.reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
}

function withMotion(option: EChartsCoreOption): EChartsCoreOption {
  return {
    animation: true,
    animationDuration: 720,
    animationEasing: 'cubicOut',
    animationDurationUpdate: 360,
    ...option,
  };
}

function averageMarkLine() {
  return {
    silent: true,
    symbol: 'none',
    lineStyle: { type: 'dashed' as const, color: '#91caff', width: 1 },
    label: { formatter: '均值 {c}', color: '#8c8c8c', fontSize: 10 },
    data: [{ type: 'average' as const }],
  };
}

export function buildChartOption(spec: ChatChartView): EChartsCoreOption {
  const palette = resolvePalette(spec);
  const primary = primaryColor(palette);

  if (spec.type === 'pie') {
    const values = spec.series[0]?.values ?? [];
    const peak = maxValueIndex(values);
    const total = seriesTotal(values);
    const wideLegend = spec.categories.length > 6;
    const center = wideLegend ? ['50%', '46%'] : ['40%', '50%'];
    return withMotion({
      color: palette,
      tooltip: { ...cardTooltip('item'), formatter: '{b}<br/>{c}（{d}%）' },
      legend: {
        type: 'scroll',
        orient: wideLegend ? 'horizontal' : 'vertical',
        right: wideLegend ? undefined : 8,
        bottom: wideLegend ? 0 : undefined,
        top: wideLegend ? undefined : 'middle',
        icon: 'circle',
        itemWidth: 8,
        itemHeight: 8,
        data: spec.categories,
        textStyle: { color: '#595959', fontSize: 11 },
      },
      series: [
        {
          type: 'pie',
          name: spec.series[0]?.name || spec.title,
          radius: ['48%', '70%'],
          center,
          padAngle: 1.2,
          animationType: 'expansion',
          animationDuration: 800,
          itemStyle: {
            borderRadius: 6,
            borderColor: '#fff',
            borderWidth: 2,
          },
          label: {
            color: '#595959',
            formatter: '{b}\n{d}%',
            fontSize: 11,
          },
          labelLine: {
            length: 12,
            length2: 8,
            lineStyle: { color: '#d9d9d9' },
          },
          data: spec.categories.map((name, index) => ({
            name,
            value: values[index] ?? 0,
            itemStyle: index === peak
              ? {
                  shadowBlur: 12,
                  shadowColor: hexToRgba(palette[index] || primary, 0.35),
                  borderWidth: 3,
                }
              : undefined,
          })),
        },
        {
          type: 'pie',
          center,
          radius: ['0%', '0%'],
          silent: true,
          tooltip: { show: false },
          legendHoverLink: false,
          label: {
            show: true,
            position: 'center',
            formatter: `{val|${formatChartNumber(total)}}\n{lbl|总计}`,
            rich: {
              val: {
                fontSize: 20,
                fontWeight: 600,
                color: primary,
                lineHeight: 26,
                align: 'center',
              },
              lbl: {
                fontSize: 11,
                color: '#8c8c8c',
                lineHeight: 16,
                align: 'center',
                padding: [2, 0, 0, 0],
              },
            },
          },
          labelLine: { show: false },
          data: [{ value: 1, name: '', itemStyle: { color: 'transparent' }, tooltip: { show: false } }],
        },
      ],
    });
  }

  if (spec.type === 'funnel') {
    const values = spec.series[0]?.values ?? [];
    return withMotion({
      color: palette,
      tooltip: cardTooltip('item'),
      legend: {
        type: 'scroll',
        bottom: 0,
        icon: 'circle',
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { color: '#595959', fontSize: 11 },
      },
      series: [
        {
          type: 'funnel',
          name: spec.series[0]?.name || spec.title,
          left: '12%',
          top: 12,
          bottom: 36,
          width: '76%',
          minSize: '18%',
          maxSize: '100%',
          gap: 6,
          sort: 'descending',
          label: {
            show: true,
            position: 'inside',
            color: '#fff',
            formatter: '{b}\n{c}',
            fontSize: 11,
          },
          itemStyle: {
            borderColor: '#fff',
            borderWidth: 2,
            borderRadius: 8,
          },
          data: spec.categories.map((name, index) => ({
            name,
            value: values[index] ?? 0,
            itemStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                { offset: 0, color: palette[index] || hexToRgba(primary, Math.max(0.4, 0.95 - index * 0.16)) },
                { offset: 1, color: hexToRgba(palette[index] || primary, 0.72) },
              ]),
            },
          })),
        },
      ],
    });
  }

  if (spec.type === 'radar') {
    const maxValue = Math.max(
      1,
      ...spec.series.flatMap((entry) => entry.values.filter((value) => Number.isFinite(value))),
    );
    return withMotion({
      color: palette,
      tooltip: cardTooltip('item'),
      legend: spec.series.length > 1
        ? { top: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { color: '#595959', fontSize: 11 } }
        : undefined,
      radar: {
        indicator: spec.categories.map((name) => ({ name, max: maxValue * 1.1 })),
        radius: '62%',
        axisName: { color: '#595959', fontSize: 11 },
        splitNumber: 4,
        splitLine: { lineStyle: { color: '#d6e4ff' } },
        splitArea: {
          areaStyle: {
            color: ['rgba(22, 119, 255, 0.04)', 'rgba(22, 119, 255, 0.01)'],
          },
        },
        axisLine: { lineStyle: { color: '#d6e4ff' } },
      },
      series: [
        {
          type: 'radar',
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { width: 2 },
          areaStyle: { opacity: 0.28 },
          data: spec.series.map((entry, index) => ({
            name: entry.name,
            value: spec.categories.map((_, valueIndex) => entry.values[valueIndex] ?? 0),
            lineStyle: { color: palette[index] || primary },
            itemStyle: { color: palette[index] || primary },
            areaStyle: { color: hexToRgba(palette[index] || primary, 0.28) },
          })),
        },
      ],
    });
  }

  if (spec.type === 'scatter') {
    const values = spec.series[0]?.values ?? [];
    const points = spec.categories.map((name, index) => {
      const parsed = Number(String(name).replace(/,/g, ''));
      const x = Number.isFinite(parsed) ? parsed : index;
      return [x, values[index] ?? 0];
    });
    return withMotion({
      color: palette,
      tooltip: cardTooltip('item'),
      grid: { left: 48, right: 16, top: 16, bottom: 32 },
      xAxis: valueAxis(),
      yAxis: valueAxis(),
      series: [
        {
          type: 'scatter',
          name: spec.series[0]?.name || spec.title,
          symbolSize: 10,
          itemStyle: {
            color: hexToRgba(primary, 0.82),
            borderColor: '#fff',
            borderWidth: 1,
            shadowBlur: 8,
            shadowColor: hexToRgba(primary, 0.25),
          },
          data: points,
        },
      ],
    });
  }

  const isHorizontal = spec.type === 'hbar';
  const isLineLike = spec.type === 'line' || spec.type === 'area';
  return withMotion({
    color: palette,
    tooltip: cardTooltip('axis'),
    legend: spec.series.length > 1
      ? { top: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { color: '#595959', fontSize: 11 } }
      : undefined,
    grid: {
      left: isHorizontal ? 80 : 44,
      right: isHorizontal ? 48 : 20,
      top: spec.series.length > 1 ? 36 : 28,
      bottom: isHorizontal ? 16 : 32,
    },
    xAxis: isHorizontal ? valueAxis() : categoryAxis(spec.categories),
    yAxis: isHorizontal
      ? { ...categoryAxis(spec.categories), inverse: true }
      : valueAxis(),
    series: spec.series.map((entry, index) => {
      const color = palette[index] || primary;
      const peak = maxValueIndex(entry.values);
      if (isLineLike) {
        return {
          type: 'line',
          name: entry.name,
          data: entry.values.map((value, dataIndex) => ({
            value,
            symbolSize: dataIndex === peak ? 11 : 8,
            itemStyle: dataIndex === peak
              ? { color, borderColor: '#fff', borderWidth: 2 }
              : { color: '#fff', borderColor: color, borderWidth: 2 },
          })),
          smooth: 0.35,
          showSymbol: true,
          symbol: 'circle',
          symbolSize: 8,
          lineStyle: { width: 3, color },
          areaStyle: { color: areaGradient(color) },
          animationDelay: (dataIndex: number) => dataIndex * 50,
          markLine: averageMarkLine(),
          markPoint: {
            symbol: 'circle',
            symbolSize: 8,
            itemStyle: { color, borderColor: '#fff', borderWidth: 2 },
            label: { formatter: '最大 {c}', color, fontSize: 10, offset: [0, -12] },
            data: [{ type: 'max' }],
          },
        };
      }
      const radius = isHorizontal ? [0, 8, 8, 0] : [8, 8, 0, 0];
      const fill = isHorizontal ? horizontalBarGradient(color) : verticalBarGradient(color);
      return {
        type: 'bar',
        name: entry.name,
        data: entry.values.map((value, dataIndex) => ({
          value,
          itemStyle: dataIndex === peak
            ? {
                borderRadius: radius,
                color: fill,
                shadowBlur: 10,
                shadowColor: hexToRgba(color, 0.32),
              }
            : {
                borderRadius: radius,
                color: hexToRgba(color, 0.82),
              },
          label: {
            fontWeight: dataIndex === peak ? 600 : 400,
            color: dataIndex === peak ? primary : '#595959',
          },
        })),
        barMaxWidth: 28,
        barGap: '28%',
        label: {
          show: true,
          position: isHorizontal ? 'right' : 'top',
          color: '#595959',
          fontSize: 11,
        },
        animationDelay: (dataIndex: number) => dataIndex * 50,
        markLine: averageMarkLine(),
      };
    }),
  });
}
