import { Component, computed, inject, input, ViewEncapsulation } from '@angular/core';
import { DomSanitizer } from '@angular/platform-browser';

import { highlightCode } from '../../utils/code-highlight';

@Component({
  selector: 'app-sql-highlight',
  template: `
    <pre class="sql-highlight__pre"><code class="language-sql" [innerHTML]="highlightedHtml()"></code></pre>
  `,
  styleUrl: './sql-highlight.component.css',
  encapsulation: ViewEncapsulation.None,
  host: {
    class: 'sql-highlight',
    '[class.sql-highlight--embedded]': 'embedded()',
  },
})
export class SqlHighlightComponent {
  private readonly sanitizer = inject(DomSanitizer);

  readonly code = input('');
  readonly embedded = input(false);

  protected readonly highlightedHtml = computed(() => {
    const text = this.code() ?? '';
    if (!text.trim()) {
      return this.sanitizer.bypassSecurityTrustHtml('');
    }
    return this.sanitizer.bypassSecurityTrustHtml(highlightCode(text, 'sql'));
  });
}
