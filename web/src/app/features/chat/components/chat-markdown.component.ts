import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  ViewEncapsulation,
  afterNextRender,
  effect,
  inject,
  input,
} from '@angular/core';
import { MarkdownComponent } from 'ngx-markdown';
import { MatDialog } from '@angular/material/dialog';

import { highlightCode } from '../../../shared/utils/code-highlight';
import { buildSqlPrecipitateDraft } from '../sql-precipitate';
import { ChatSqlPrecipitateDialogComponent } from './chat-sql-precipitate-dialog.component';

@Component({
  selector: 'app-chat-markdown',
  imports: [MarkdownComponent],
  template: `
    <div class="chat-markdown" [class.chat-markdown--query-cards]="enhanceQueryCards()">
      <markdown [data]="content()" (ready)="onMarkdownReady()" />
    </div>
  `,
  styleUrl: './chat-markdown.component.css',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatMarkdownComponent {
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly dialog = inject(MatDialog);
  readonly content = input.required<string>();
  /** Only enable in main chat result text; trace/debug markdown should stay plain. */
  readonly enhanceQueryCards = input(false);
  /** Skip heavy DOM enhancement while the assistant message is still streaming. */
  readonly streaming = input(false);
  /** 仅助手正文里的 SQL 代码块显示沉淀按钮。 */
  readonly allowSqlPrecipitate = input(false);
  /** 本轮用户问题，用作沉淀样例的默认名称。 */
  readonly precipitateName = input('');

  constructor() {
    afterNextRender(() => {
      if (!this.streaming()) {
        this.scheduleEnhance();
      }
    });

    effect(() => {
      this.content();
      this.enhanceQueryCards();
      this.allowSqlPrecipitate();
      this.precipitateName();
      if (this.streaming()) {
        return;
      }
      this.scheduleEnhance();
    });
  }

  protected onMarkdownReady(): void {
    if (!this.streaming()) {
      this.scheduleEnhance();
    }
  }

  private scheduleEnhance(): void {
    queueMicrotask(() => {
      requestAnimationFrame(() => {
        this.enhanceCodeBlocks();
        this.enhanceTables();
        this.enhanceQuerySections();
      });
    });
  }

  private enhanceCodeBlocks(): void {
    const root = this.host.nativeElement;
    root.querySelectorAll('pre').forEach((pre: HTMLPreElement) => {
      const code = pre.querySelector('code');
      const langMatch = (code?.className ?? pre.className).match(/language-([\w-]+)/);
      const lang = (langMatch?.[1] ?? 'text').toUpperCase();
      const plainText = this.highlightPreCode(pre);
      const text = plainText ?? code?.textContent ?? pre.textContent ?? '';

      if (pre.closest('.chat-code-block')) {
        this.ensureSqlPrecipitateButton(pre, text, lang);
        return;
      }

      const block = document.createElement('div');
      block.className = 'chat-code-block';

      const header = document.createElement('div');
      header.className = 'chat-code-block__header';

      const label = document.createElement('span');
      label.className = 'chat-code-block__lang';
      label.textContent = lang;

      header.append(label, this.createCodeActions(text, lang));
      pre.parentNode?.insertBefore(block, pre);
      block.append(header, pre);
    });
  }

  private createCodeActions(text: string, lang: string): HTMLDivElement {
    const actions = document.createElement('div');
    actions.className = 'chat-code-block__actions';
    const precipitate = this.createSqlPrecipitateButton(text, lang);
    if (precipitate) {
      actions.append(precipitate);
    }
    actions.append(this.createCopyButton(text));
    return actions;
  }

  private ensureSqlPrecipitateButton(pre: HTMLPreElement, text: string, lang: string): void {
    const header = pre.parentElement?.querySelector(':scope > .chat-code-block__header');
    const actions = header?.querySelector('.chat-code-block__actions');
    if (!actions || actions.querySelector('.chat-code-block__precipitate')) {
      return;
    }
    const button = this.createSqlPrecipitateButton(text, lang);
    if (!button) {
      return;
    }
    const copy = actions.querySelector('.chat-code-block__copy');
    if (copy) {
      actions.insertBefore(button, copy);
    } else {
      actions.append(button);
    }
  }

  private createSqlPrecipitateButton(text: string, lang: string): HTMLButtonElement | null {
    if (!this.allowSqlPrecipitate() || lang.toLowerCase() !== 'sql' || !text.trim()) {
      return null;
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chat-code-block__copy chat-code-block__precipitate';
    button.setAttribute('aria-label', '沉淀为历史 SQL');
    button.title = '沉淀为历史 SQL';
    button.innerHTML =
      '<span class="material-symbols-outlined" aria-hidden="true">bookmark_add</span>';
    button.addEventListener('click', () => {
      this.dialog.open(ChatSqlPrecipitateDialogComponent, {
        data: buildSqlPrecipitateDraft(this.content(), text, this.precipitateName()),
        panelClass: 'app-sql-precipitate-dialog-panel',
        backdropClass: 'app-dialog-backdrop',
        autoFocus: 'first-tabbable',
        restoreFocus: true,
        width: 'min(40rem, calc(100vw - 2rem))',
        maxWidth: 'calc(100vw - 2rem)',
        maxHeight: '86vh',
      });
    });
    return button;
  }

  private createCopyButton(text: string): HTMLButtonElement {
    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'chat-code-block__copy';
    copyBtn.setAttribute('aria-label', '复制代码');
    copyBtn.innerHTML =
      '<span class="material-symbols-outlined" aria-hidden="true">content_copy</span>';
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(text);
        copyBtn.classList.add('is-copied');
        copyBtn.innerHTML =
          '<span class="material-symbols-outlined" aria-hidden="true">check</span>';
        window.setTimeout(() => {
          copyBtn.classList.remove('is-copied');
          copyBtn.innerHTML =
            '<span class="material-symbols-outlined" aria-hidden="true">content_copy</span>';
        }, 1600);
      } catch {
        /* 忽略剪贴板写入失败 */
      }
    });
    return copyBtn;
  }

  private highlightPreCode(pre: HTMLPreElement): string {
    const code = pre.querySelector('code');
    const langMatch = (code?.className ?? pre.className).match(/language-([\w-]+)/);
    const lang = langMatch?.[1] ?? 'text';
    const text = code?.textContent ?? pre.textContent ?? '';
    if (!text.trim()) {
      return text;
    }

    const html = highlightCode(text, lang);
    if (code) {
      code.innerHTML = html;
      if (!/language-/.test(code.className)) {
        code.className = `language-${lang}`;
      }
    } else {
      pre.innerHTML = `<code class="language-${lang}">${html}</code>`;
    }
    return text;
  }

  private enhanceTables(): void {
    const root = this.host.nativeElement;
    root.querySelectorAll('table').forEach((table: HTMLTableElement) => {
      if (table.closest('.chat-data-table')) {
        return;
      }

      (Array.from(table.querySelectorAll('tbody td')) as HTMLTableCellElement[]).forEach((cell) => {
        const text = (cell.textContent ?? '').trim().replace(/,/g, '');
        if (/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text)) {
          cell.classList.add('chat-data-table__num');
        }
      });

      const headerCells = table.querySelectorAll('thead th');
      headerCells.forEach((header, columnIndex) => {
        const bodyRows = Array.from(table.querySelectorAll('tbody tr')) as HTMLTableRowElement[];
        if (!bodyRows.length) {
          return;
        }
        const allNumeric = bodyRows.every((row) => {
          const cell = row.cells.item(columnIndex);
          if (!cell) {
            return false;
          }
          const text = (cell.textContent ?? '').trim().replace(/,/g, '');
          return /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text);
        });
        if (allNumeric) {
          (header as HTMLTableCellElement).classList.add('chat-data-table__num');
        }
      });

      const wrap = document.createElement('div');
      wrap.className = 'chat-data-table';

      const scroll = document.createElement('div');
      scroll.className = 'chat-data-table__scroll';

      table.parentNode?.insertBefore(wrap, table);
      scroll.appendChild(table);
      wrap.appendChild(scroll);
    });
  }

  private enhanceQuerySections(): void {
    if (!this.enhanceQueryCards()) {
      return;
    }

    const markdownHost = this.host.nativeElement.querySelector('markdown');
    if (!markdownHost || markdownHost.querySelector('.chat-query-card')) {
      return;
    }

    markdownHost.querySelectorAll('hr').forEach((hr: Element) => hr.remove());

    type SectionType = 'validation-ok' | 'validation-fail' | 'result';

    interface SectionSpec {
      type: SectionType;
      label: string;
      icon: string;
      heading: HTMLHeadingElement;
      contentNodes: ChildNode[];
    }

    const classifyHeading = (title: string): Omit<SectionSpec, 'heading' | 'contentNodes'> | null => {
      if (/SQL 合法性检查/.test(title)) {
        const failed = /❌/.test(title);
        return {
          type: failed ? 'validation-fail' : 'validation-ok',
          label: 'SQL 合法性检查',
          icon: failed ? 'error' : 'verified',
        };
      }
      if (/查询结果/.test(title)) {
        const label = title.replace(/^[#\s📊✅❌]+/u, '').trim() || '查询结果';
        return { type: 'result', label, icon: 'table_chart' };
      }
      return null;
    };

    const specs: SectionSpec[] = [];

    markdownHost.querySelectorAll('h2').forEach((heading: Element) => {
      const meta = classifyHeading(heading.textContent ?? '');
      if (!meta) {
        return;
      }

      const htmlHeading = heading as HTMLHeadingElement;

      const contentNodes: ChildNode[] = [];
      let sibling = heading.nextSibling;
      while (sibling) {
        if (sibling instanceof HTMLElement && sibling.tagName === 'H2') {
          break;
        }
        if (sibling instanceof HTMLElement && sibling.tagName === 'HR') {
          const next = sibling.nextSibling;
          sibling.remove();
          sibling = next;
          continue;
        }
        const next = sibling.nextSibling;
        contentNodes.push(sibling);
        sibling = next;
      }

      specs.push({
        ...meta,
        heading: htmlHeading,
        contentNodes,
      });
    });

    if (!specs.length) {
      return;
    }

    const ordered = [
      ...specs.filter((section) => section.type !== 'result'),
      ...specs.filter((section) => section.type === 'result'),
    ];

    for (const section of ordered) {
      const card = document.createElement('div');
      card.className = `chat-query-card chat-query-card--${section.type}`;

      const head = document.createElement('div');
      head.className = 'chat-query-card__head';
      head.innerHTML = `<span class="chat-query-card__icon material-symbols-outlined" aria-hidden="true">${section.icon}</span><span class="chat-query-card__title">${section.label}</span>`;

      const body = document.createElement('div');
      body.className = 'chat-query-card__body';
      for (const node of section.contentNodes) {
        body.appendChild(node);
      }

      if (!body.textContent?.trim() && !body.querySelector('table, .chat-data-table')) {
        const empty = document.createElement('p');
        empty.className = 'chat-query-card__empty';
        empty.textContent =
          section.type === 'result' ? '查询已执行，未返回数据行。' : '检查已完成。';
        body.appendChild(empty);
      }

      card.append(head, body);
      section.heading.remove();
      markdownHost.appendChild(card);
    }

    markdownHost.querySelectorAll('hr').forEach((hr: Element) => hr.remove());
  }
}
