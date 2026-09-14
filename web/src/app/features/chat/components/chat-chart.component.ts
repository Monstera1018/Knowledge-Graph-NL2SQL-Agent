import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  afterNextRender,
  effect,
  input,
  viewChild,
} from '@angular/core';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsCoreOption, EChartsType } from 'echarts/core';

import type { ChatChartView } from '../chat-chart';
import { DEFAULT_CHART_PALETTE } from '../chat-chart';

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

@Component({
  selector: 'app-chat-chart',
  template: `<div class="chat-chart__host" #host></div>`,
  styleUrl: './chat-chart.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatChartComponent implements OnDestroy {
  readonly spec = input.required<ChatChartView>();
  private readonly host = viewChild.required<ElementRef<HTMLDivElement>>('host');

  private chart: EChartsType | null = null;
  private resizeObserver: ResizeObserver | null = null;

  constructor() {
    afterNextRender(() => {
      this.ensureChart();
      this.render();
    });

    effect(() => {
      this.spec();
      this.render();
    });
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.chart?.dispose();
    this.chart = null;
  }

  private ensureChart(): void {
    if (this.chart) {
      return;
    }
    const el = this.host().nativeElement;
    this.chart = echarts.init(el);
    this.resizeObserver = new ResizeObserver(() => this.chart?.resize());
    this.resizeObserver.observe(el);
  }

  private render(): void {
    if (!this.chart) {
      return;
    }
    this.chart.setOption(buildChartOption(this.spec()), true);
  }
}

function buildChartOption(spec: ChatChartView): EChartsCoreOption {
  const palette = spec.palette.length ? spec.palette : DEFAULT_CHART_PALETTE;
  if (spec.type === 'pie') {
    const values = spec.series[0]?.values ?? [];
    return {
      color: palette,
      tooltip: { trigger: 'item' },
      legend: { type: 'scroll', bottom: 0 },
      series: [
        {
          type: 'pie',
          name: spec.series[0]?.name || spec.title,
          radius: ['36%', '64%'],
          data: spec.categories.map((name, index) => ({
            name,
            value: values[index] ?? 0,
          })),
        },
      ],
    };
  }

  return {
    color: palette,
    tooltip: { trigger: 'axis' },
    legend: spec.series.length > 1 ? { top: 0 } : undefined,
    grid: { left: 40, right: 16, top: spec.series.length > 1 ? 36 : 16, bottom: 32 },
    xAxis: {
      type: 'category',
      data: spec.categories,
      axisLabel: { hideOverlap: true },
    },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#e2e8f0' } } },
    series: spec.series.map((entry) => ({
      type: spec.type,
      name: entry.name,
      data: entry.values,
      smooth: spec.type === 'line',
      barMaxWidth: 36,
    })),
  };
}
