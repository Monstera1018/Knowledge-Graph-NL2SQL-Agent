import {
  AfterViewInit,
  Directive,
  ElementRef,
  OnDestroy,
  inject,
  input,
} from '@angular/core';
import { MatTooltip, TooltipPosition } from '@angular/material/tooltip';

/**
 * 文本被省略号截断时才显示 Tooltip（样式见全局 app-tooltip）。
 * 用法：[appTruncateTooltip]="全文" 或写在已有文本的元素上。
 */
@Directive({
  selector: '[appTruncateTooltip]',
  standalone: true,
  hostDirectives: [
    {
      directive: MatTooltip,
      inputs: ['matTooltipPosition: appTruncateTooltipPosition'],
    },
  ],
})
export class AppTruncateTooltipDirective implements AfterViewInit, OnDestroy {
  /** 提示文案，默认取元素文本 */
  readonly appTruncateTooltip = input<string>();
  readonly appTruncateTooltipPosition = input<TooltipPosition>('right');

  private readonly elementRef = inject(ElementRef<HTMLElement>);
  private readonly tooltip = inject(MatTooltip);
  private resizeObserver: ResizeObserver | null = null;
  private removeMouseEnter?: () => void;

  ngAfterViewInit(): void {
    const element = this.elementRef.nativeElement;
    this.tooltip.tooltipClass = 'app-tooltip app-tooltip--truncate';
    this.tooltip.showDelay = 400;
    this.tooltip.position = this.appTruncateTooltipPosition();

    const sync = (): void => {
      const text = (this.appTruncateTooltip() ?? element.textContent ?? '').trim();
      this.tooltip.message = text;
      this.tooltip.disabled = !text || !this.isOverflowing(element);
    };

    sync();
    const onEnter = (): void => sync();
    element.addEventListener('mouseenter', onEnter);
    this.removeMouseEnter = () => element.removeEventListener('mouseenter', onEnter);

    this.resizeObserver = new ResizeObserver(() => sync());
    this.resizeObserver.observe(element);
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.removeMouseEnter?.();
  }

  private isOverflowing(element: HTMLElement): boolean {
    return element.scrollWidth > element.clientWidth + 1;
  }
}
