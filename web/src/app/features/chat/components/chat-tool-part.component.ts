import { JsonPipe } from '@angular/common';
import { Component, input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { ToolPart } from '../../../core/models/chat.models';

type ContextSection = {
  title: string;
  items: string[];
  content?: string;
};

type SelectedTableDetail = {
  name: string;
  comment?: string;
  description?: string;
};

@Component({
  selector: 'app-chat-tool-part',
  imports: [MatIconModule, JsonPipe],
  templateUrl: './chat-tool-part.component.html',
  styleUrl: './chat-part.component.css',
})
export class ChatToolPartComponent {
  readonly part = input.required<ToolPart>();

  protected stageLabel(): string {
    const labels: Record<string, string> = {
      extractor: '解析问题',
      search: '检索上下文',
      table_selector: '选择数据表',
      route_after_table_selection: '判断选表结果',
      format_no_table_response: '未匹配到数据表',
      load_sql_schema_context: '汇总表结构',
      sql_generator: '生成 SQL',
      validate_sql: '校验 SQL',
      execute_sql: '执行 SQL',
    };
    return labels[this.part().name] || this.part().title || this.part().name;
  }

  protected phaseText(): string {
    return this.part().phase === 'call' ? '执行中' : '已完成';
  }

  protected businessTitle(): string {
    const titles: Record<string, string> = {
      extractor: '系统正在理解你的问题',
      search: '系统正在查找相关业务资料',
      table_selector: '系统正在判断需要哪些数据表',
      route_after_table_selection: '系统正在确认是否已匹配到可用表',
      format_no_table_response: '系统未找到可用数据表',
      load_sql_schema_context: '系统正在整理可查询的表结构',
      sql_generator: '系统正在生成查询逻辑',
      validate_sql: '系统正在检查查询是否安全可执行',
      execute_sql: '系统正在查询数据',
    };
    return titles[this.part().name] || '系统正在处理该步骤';
  }

  protected businessSummary(): string {
    if (this.part().phase === 'call') {
      return this.callSummary();
    }

    if (this.part().name === 'extractor') {
      return this.extractorSummary();
    }
    if (this.part().name === 'search') {
      return this.searchSummary();
    }
    if (this.part().name === 'table_selector') {
      return this.tableSelectorSummary();
    }
    if (this.part().name === 'route_after_table_selection') {
      return '系统已检查选表结果，若未匹配到可用表，将停止生成 SQL。';
    }
    if (this.part().name === 'format_no_table_response') {
      return '本次没有识别到足够明确的数据表，已停止生成 SQL。';
    }
    if (this.part().name === 'load_sql_schema_context') {
      return this.schemaContextSummary();
    }
    if (this.part().name === 'sql_generator') {
      return '已生成本次查询所需的 SQL 语句和查询逻辑，页面正文中会展示生成结果。';
    }
    if (this.part().name === 'validate_sql') {
      return this.validateSqlSummary();
    }
    if (this.part().name === 'execute_sql') {
      return this.executeSqlSummary();
    }

    return '该步骤已处理完成。';
  }

  protected businessItems(): string[] {
    if (this.part().phase === 'call') {
      return [];
    }

    if (this.part().name === 'extractor') {
      return this.extractorItems();
    }
    if (this.part().name === 'search') {
      return this.contextSections().length
        ? [`已召回 ${this.contextSections().length} 类上下文，详情见下方分类结果。`]
        : [
            '会从向量库中召回相关的数据表、字段、枚举值、业务知识、历史 SQL 和表关系。',
            '这些资料作为后续选表和生成 SQL 的参考，不会直接作为最终答案展示。',
          ];
    }
    if (this.part().name === 'table_selector') {
      const details = this.tableSelectionDetails();
      if (details.length) {
        return [`已选择 ${details.length} 张数据表，详情见下方选表结果。`];
      }
      return [
        '根据问题中的业务对象、地区条件、指标口径和检索上下文选择候选表。',
        '选表详情会在“选表结果”卡片中展示，包括表中文名、英文名和选择原因。',
      ];
    }
    if (this.part().name === 'load_sql_schema_context') {
      return this.contextSections().length
        ? [`已汇总 ${this.contextSections().length} 类表结构上下文，详情见下方分类结果。`]
        : [
            '整理已选表的字段、字段说明、关联关系和可用业务约束。',
            '只把后续生成 SQL 需要的表结构上下文交给模型，减少无关信息干扰。',
          ];
    }
    if (this.part().name === 'sql_generator') {
      return [
        '把自然语言问题转换成只读查询 SQL。',
        '只允许使用本轮已经选择的数据表。',
        '会尽量为输出字段设置中文列名，方便业务人员查看结果。',
      ];
    }
    if (this.part().name === 'validate_sql') {
      return this.validateSqlItems();
    }
    if (this.part().name === 'execute_sql') {
      return ['SQL 已提交到业务数据库执行，查询结果会在下方结果区域展示。'];
    }

    return [];
  }

  protected showBusinessSummary(): boolean {
    return [
      'extractor',
      'search',
      'table_selector',
      'route_after_table_selection',
      'format_no_table_response',
      'load_sql_schema_context',
      'sql_generator',
      'validate_sql',
      'execute_sql',
    ].includes(this.part().name);
  }

  protected hasTechnicalDetails(): boolean {
    return !!this.part().input || (this.part().output !== undefined && this.part().output !== null);
  }

  protected contextSections(): ContextSection[] {
    const sections = this.outputObject()?.['sections'];
    if (!Array.isArray(sections)) {
      return [];
    }
    return sections
      .map((item): ContextSection | null => {
        if (!item || typeof item !== 'object') {
          return null;
        }
        const record = item as Record<string, unknown>;
        const title = record['title'];
        if (typeof title !== 'string' || !title.trim()) {
          return null;
        }
        return {
          title: title.trim(),
          items: this.stringArray(record['items']).map((value) => this.cleanMarkdown(value)),
          content: typeof record['content'] === 'string' ? record['content'] : '',
        };
      })
      .filter((item): item is ContextSection => !!item);
  }

  protected tableSelectionDetails(): SelectedTableDetail[] {
    const result = this.outputRecord('result');
    const details = result?.['table_details'];
    if (Array.isArray(details)) {
      const normalized = details
        .map((item): SelectedTableDetail | null => {
          if (!item || typeof item !== 'object') {
            return null;
          }
          const record = item as Record<string, unknown>;
          const name = record['name'];
          if (typeof name !== 'string' || !name.trim()) {
            return null;
          }
          const comment = record['comment'];
          const description = record['description'];
          return {
            name: name.trim(),
            comment: typeof comment === 'string' ? comment.trim() : '',
            description: typeof description === 'string' ? description.trim() : '',
          };
        })
        .filter((item): item is SelectedTableDetail => !!item);
      if (normalized.length) {
        return normalized;
      }
    }
    return this.stringArray(result?.['tables']).map((name) => ({ name }));
  }

  protected tableSelectionReason(): string {
    const result = this.outputRecord('result');
    return this.text(result, 'reason') || this.text(result, 'reasoning');
  }

  private callSummary(): string {
    if (this.part().name === 'extractor') {
      return '正在识别问题中的地区、对象、指标和查询意图。';
    }
    if (this.part().name === 'search') {
      return '正在检索与问题相关的数据表、字段、枚举值、业务知识、历史 SQL 和表关系。';
    }
    if (this.part().name === 'table_selector') {
      return '正在根据检索到的表、字段、枚举值、业务知识和表关系判断应该使用哪些数据表。';
    }
    if (this.part().name === 'route_after_table_selection') {
      return '正在确认选表结果是否可以进入 SQL 生成。';
    }
    if (this.part().name === 'format_no_table_response') {
      return '正在整理未匹配到数据表的提示。';
    }
    if (this.part().name === 'load_sql_schema_context') {
      return '正在汇总已选表的字段清单、字段含义和表之间的关联关系。';
    }
    if (this.part().name === 'sql_generator') {
      return '正在根据已选表结构和业务规则生成查询 SQL。';
    }
    if (this.part().name === 'validate_sql') {
      return '正在检查 SQL 是否只读、安全，表名和字段是否存在。';
    }
    if (this.part().name === 'execute_sql') {
      return '正在执行业务查询并整理返回结果。';
    }
    return '正在处理该步骤。';
  }

  private extractorSummary(): string {
    const result = this.outputRecord('result');
    const intent = this.text(result, 'intent');
    const query = this.text(result, 'retrieval_query');
    if (intent || query) {
      return `系统已理解问题：${intent || query}`;
    }
    return '系统已完成问题理解，提取出后续检索需要的关键词和查询意图。';
  }

  private extractorItems(): string[] {
    const result = this.outputRecord('result');
    const query = this.text(result, 'retrieval_query');
    const keywords = this.stringArray(result?.['keywords']);
    const items: string[] = [];
    if (query) {
      items.push(`检索表达：${query}`);
    }
    if (keywords.length) {
      items.push(`关键词：${keywords.join('、')}`);
    }
    return items;
  }

  private tableSelectorSummary(): string {
    const details = this.tableSelectionDetails();
    if (details.length) {
      return `系统已选择 ${details.length} 张候选表用于回答该问题。`;
    }
    return '系统已完成选表判断，具体表名和选择原因请查看下方“选表结果”。';
  }

  private searchSummary(): string {
    const sections = this.contextSections();
    if (sections.length) {
      const labels = sections.map((item) => item.title).join('、');
      return `系统已完成上下文检索，召回了 ${labels} 等资料。`;
    }
    return '系统已完成上下文检索，召回了与问题相关的表、字段、值、知识和历史查询资料。';
  }

  private schemaContextSummary(): string {
    const sections = this.contextSections();
    if (sections.length) {
      const labels = sections.map((item) => item.title).join('、');
      return `系统已整理本次查询可使用的表结构上下文，包括 ${labels}。`;
    }
    return '系统已汇总已选表的结构信息，包括字段、字段说明和必要的关联关系。';
  }

  private validateSqlSummary(): string {
    const result = this.outputRecord('result');
    if (result?.['ok'] === true) {
      return 'SQL 校验通过，可以安全执行查询。';
    }
    if (result?.['ok'] === false) {
      return 'SQL 校验未通过，系统会根据错误信息尝试重新生成或修正。';
    }
    return 'SQL 校验步骤已完成。';
  }

  private validateSqlItems(): string[] {
    const result = this.outputRecord('result');
    const errors = this.stringArray(result?.['errors']);
    if (errors.length) {
      return errors.map((item) => `问题：${item}`);
    }
    if (result?.['ok'] === true) {
      return [
        '已确认 SQL 是只读查询，不会修改数据。',
        '已确认使用的表和字段存在。',
        '已确认 SQL 没有使用本轮未选择的数据表。',
      ];
    }
    return [];
  }

  private executeSqlSummary(): string {
    const output = this.outputObject();
    if (output?.['status'] === 'ok') {
      return '查询执行完成，结果已整理为业务表格展示。';
    }
    if (output?.['status'] === 'error') {
      return '查询执行失败，请查看技术详情中的错误信息。';
    }
    return '查询执行步骤已完成。';
  }

  private outputObject(): Record<string, unknown> | null {
    const value = this.part().output;
    return value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
  }

  private outputRecord(key: string): Record<string, unknown> | null {
    const value = this.outputObject()?.[key];
    return value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
  }

  private text(record: Record<string, unknown> | null, key: string): string {
    const value = record?.[key];
    return typeof value === 'string' ? value.trim() : '';
  }

  private stringArray(value: unknown): string[] {
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
      : [];
  }

  private cleanMarkdown(value: string): string {
    return value
      .replace(/\*\*/g, '')
      .replace(/`/g, '')
      .split('锛?')
      .join('：')
      .split('銆?')
      .join('、')
      .trim();
  }
}
