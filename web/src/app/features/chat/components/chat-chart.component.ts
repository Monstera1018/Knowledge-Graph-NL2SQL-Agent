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
import { BarChart, FunnelChart, LineChart, PieChart, RadarChart, ScatterChart } from 'echarts/charts';
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsType } from 'echarts/core';

import { buildChartOption } from '../chat-chart-option';
import type { ChatChartView } from '../chat-chart';

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  RadarChart,
  ScatterChart,
  FunnelChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
]);

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
