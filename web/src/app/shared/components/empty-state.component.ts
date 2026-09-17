import { Component, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

@Component({
  selector: 'app-empty-state',
  imports: [MatIconModule],
  template: `
    <div
      class="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-white px-6 py-12 text-center"
    >
      <mat-icon class="icon-lg mb-3 !h-10 !w-10 !text-[40px] text-slate-300">{{ icon() }}</mat-icon>
      <p class="text-sm font-medium text-slate-700">{{ title() }}</p>
      @if (description()) {
        <p class="mt-1 max-w-sm text-sm text-slate-500">{{ description() }}</p>
      }
      @if (hasActions) {
        <div class="mt-4 flex gap-2">
          <ng-content />
        </div>
      }
    </div>
  `,
})
export class EmptyStateComponent {
  readonly icon = input('inbox');
  readonly title = input.required<string>();
  readonly description = input<string>('');

  protected get hasActions(): boolean {
    return false;
  }
}
