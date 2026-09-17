import { JsonPipe } from '@angular/common';
import { Component, computed, effect, input, signal } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

import type { ToolPart } from '../../../core/models/chat.models';
import { formatToolDurationMs, toolPartDurationMs } from '../tool-duration';

type ContextSection = {
  title: string;
  items: string[];
  content?: string;
  sqlSamples?: SqlContextSample[];
};

type SqlContextSample = {
  name: string;
  logic: string;
  content: string;
  relatedTables: string[];
};

type SelectedTableDetail = {
  name: string;
  comment?: string;
  description?: string;
};

type TableContextGroup = {
  tableName: string;
  comment?: string;
  summary?: string;
  items: string[];
};

type KnowledgeContextGroup = {
  title: string;
  items: string[];
};

@Component({
  selector: 'app-chat-tool-part',
  imports: [MatIconModule, JsonPipe],
  templateUrl: './chat-tool-part.component.html',
  styleUrl: './chat-part.component.css',
})
export class ChatToolPartComponent {
  readonly part = input.required<ToolPart>();
  private readonly expandedSearchContextSections = signal<ReadonlySet<string>>(new Set());
  private readonly nowMs = signal(Date.now());

  constructor() {
    effect((onCleanup) => {
      if (this.part().phase !== 'call') {
        return;
      }
      this.nowMs.set(Date.now());
      const timer = setInterval(() => this.nowMs.set(Date.now()), 100);
      onCleanup(() => clearInterval(timer));
    });
  }

  protected readonly phaseText = computed(() => {
    const durationMs = this.displayedDurationMs();
    if (durationMs != null) {
      return formatToolDurationMs(durationMs);
    }
    return this.part().phase === 'call' ? '执行中' : '已完成';
  });

  protected isContextSectionExpanded(section: ContextSection): boolean {
    return this.part().name !== 'search' || this.expandedSearchContextSections().has(section.title);
  }

  protected onContextSectionToggle(section: ContextSection, event: Event): void {
    if (this.part().name !== 'search') {
      return;
    }

    const details = event.currentTarget as HTMLDetailsElement;
    this.expandedSearchContextSections.update((current) => {
      const next = new Set(current);
      if (details.open) {
        next.add(section.title);
      } else {
        next.delete(section.title);
      }
      return next;
    });
  }

  protected stageLabel(): string {
    const labels: Record<string, string> = {
      extractor: '解析问题',
      planner: '规划步骤',
      parse_plan: '规划步骤',
      task_plan: '规划步骤',
      search: '检索上下文',
      table_context: '确定数据表与表结构',
      table_selector: '选择数据表',
      route_after_table_selection: '判断选表结果',
      format_no_table_response: '未匹配到数据表',
      load_sql_schema_context: '汇总表结构',
      sql_generator: '生成 SQL',
      validate_sql: '校验 SQL',
      execute_sql: '执行 SQL',
      chart_analysis: '分析图表',
      prepare_chart_planner_input: '分析图表',
      chart_planner: '分析图表',
      parse_chart_spec: '分析图表',
      format_no_chart_snapshot_response: '分析图表',
      report_analysis: '分析报告',
      prepare_report_planner_input: '分析报告',
      report_planner: '分析报告',
      parse_report_spec: '分析报告',
    };
    return labels[this.part().name] || this.part().title || this.part().name;
  }

  private displayedDurationMs(): number | undefined {
    const part = this.part();
    if (part.phase === 'call') {
      if (!part.started_at) {
        return undefined;
      }
      const started = Date.parse(part.started_at);
      if (!Number.isFinite(started)) {
        return undefined;
      }
      return Math.max(0, this.nowMs() - started);
    }
    return toolPartDurationMs(part);
  }

  protected businessTitle(): string {
    const titles: Record<string, string> = {
      extractor: '系统正在理解你的问题',
      planner: '系统正在决定下一步做什么',
      parse_plan: '系统正在决定下一步做什么',
      task_plan: '系统正在决定下一步做什么',
      search: '系统正在查找相关业务资料',
      table_context: '系统正在确定可查询的数据表与字段结构',
      table_selector: '系统正在判断需要哪些数据表',
      route_after_table_selection: '系统正在确认是否已匹配到可用表',
      format_no_table_response: '系统未找到可用数据表',
      load_sql_schema_context: '系统正在整理可查询的表结构',
      sql_generator: '系统正在生成查询逻辑',
      validate_sql: '系统正在检查查询是否安全可执行',
      execute_sql: '系统正在查询数据',
      chart_analysis: '系统正在规划图表',
      report_analysis: '系统正在生成分析报告',
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
    if (this.part().name === 'task_plan' || this.part().name === 'planner' || this.part().name === 'parse_plan') {
      return this.taskPlanSummary();
    }
    if (this.part().name === 'search') {
      return this.searchSummary();
    }
    if (this.part().name === 'table_context') {
      return this.schemaContextSummary() || this.tableSelectorSummary();
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
    if (this.part().name === 'chart_analysis') {
      return this.chartAnalysisSummary();
    }
    if (this.part().name === 'report_analysis') {
      return this.reportAnalysisSummary();
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
    if (this.part().name === 'task_plan' || this.part().name === 'planner' || this.part().name === 'parse_plan') {
      return this.taskPlanItems();
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
    if (this.part().name === 'table_context') {
      const details = this.tableSelectionDetails();
      const sections = this.contextSections();
      const items: string[] = [];
      if (details.length) {
        items.push(`已选择 ${details.length} 张数据表，下方展示表中文名、英文名和选表说明。`);
      }
      if (sections.length) {
        items.push(`已整理 ${sections.length} 类表结构上下文，用于后续生成 SQL。`);
      }
      if (!items.length) {
        items.push('正在根据检索上下文确定最终使用的数据表，并整理字段、枚举、表关系和业务知识。');
      }
      return items;
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
    if (this.part().name === 'chart_analysis') {
      const summary = this.chartAnalysisSummary();
      return summary ? [summary] : ['已根据当前查询结果规划图表。'];
    }
    if (this.part().name === 'report_analysis') {
      const summary = this.reportAnalysisSummary();
      return summary ? [summary] : ['已根据当前查询结果生成分析报告。'];
    }

    return [];
  }

  protected showBusinessSummary(): boolean {
    return [
      'extractor',
      'planner',
      'parse_plan',
      'task_plan',
      'search',
      'table_context',
      'table_selector',
      'route_after_table_selection',
      'format_no_table_response',
      'load_sql_schema_context',
      'sql_generator',
      'validate_sql',
      'execute_sql',
      'chart_analysis',
      'report_analysis',
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
    const normalized = sections
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
          sqlSamples: this.sqlSamplesFromRecord(record),
        };
      })
      .filter((item): item is ContextSection => !!item);

    if (this.part().name !== 'table_context') {
      return normalized;
    }
    return normalized.filter((section) => !this.isRedundantTableContextSection(section.title));
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

  protected isTableContextSection(section: ContextSection): boolean {
    const normalized = this.normalizeSectionTitle(section.title);
    return normalized === '数据表' || normalized === '库表结构(供生成SQL)';
  }

  protected isKnowledgeContextSection(section: ContextSection): boolean {
    return this.normalizeSectionTitle(section.title) === '业务知识';
  }

  protected isSqlContextSection(section: ContextSection): boolean {
    return this.normalizeSectionTitle(section.title) === 'SQL';
  }

  protected sqlContextSamples(section: ContextSection): SqlContextSample[] {
    return section.sqlSamples ?? [];
  }

  protected tableContextGroups(section: ContextSection): TableContextGroup[] {
    const groups: TableContextGroup[] = [];
    let current: TableContextGroup | null = null;

    for (const item of section.items) {
      if (this.isTableNameItem(item)) {
        current = {
          tableName: item,
          items: [],
        };
        groups.push(current);
        continue;
      }

      if (!current) {
        current = {
          tableName: '相关内容',
          items: [],
        };
        groups.push(current);
      }

      if (this.isCommentItem(item) && !current.comment) {
        current.comment = item.replace(/^说明[:：]\s*/, '').trim();
        continue;
      }
      if (this.isSummaryItem(item) && !current.summary) {
        current.summary = item.replace(/^摘要[:：]\s*/, '').trim();
        continue;
      }
      current.items.push(item);
    }

    return groups;
  }

  protected knowledgeContextGroups(section: ContextSection): KnowledgeContextGroup[] {
    const dedicated: string[] = [];
    const general: string[] = [];

    for (const item of section.items) {
      if (this.isDedicatedKnowledge(item)) {
        dedicated.push(item);
      } else {
        general.push(item);
      }
    }

    return [
      { title: `专用知识（已绑定表/字段） ${dedicated.length} 条`, items: dedicated },
      { title: `通用知识（未绑定表字段） ${general.length} 条`, items: general },
    ].filter((group) => group.items.length > 0);
  }

  private isRedundantTableContextSection(title: string): boolean {
    const normalized = title.trim().replace(/\s+/g, '');
    return ['用户问题', '选表说明', '候选表'].includes(normalized);
  }

  private normalizeSectionTitle(title: string): string {
    return title
      .trim()
      .replace(/\s+/g, '')
      .replace(/（/g, '(')
      .replace(/）/g, ')');
  }

  private isTableNameItem(item: string): boolean {
    const text = item.trim();
    return /^[A-Za-z][A-Za-z0-9_.$]*$/.test(text) && text.includes('_');
  }

  private isCommentItem(item: string): boolean {
    return /^说明[:：]/.test(item.trim());
  }

  private isSummaryItem(item: string): boolean {
    return /^摘要[:：]/.test(item.trim());
  }

  private isDedicatedKnowledge(item: string): boolean {
    const text = item.trim();
    if (!text || text.includes('未绑定表字段')) {
      return false;
    }
    return /(^|[^A-Za-z0-9_])dws_[A-Za-z0-9_.$]*/.test(text)
      || text.includes('关联')
      || /[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*/.test(text);
  }

  private callSummary(): string {
    if (this.part().name === 'extractor') {
      return '正在识别问题中的地区、对象、指标和查询意图。';
    }
    if (this.part().name === 'task_plan' || this.part().name === 'planner' || this.part().name === 'parse_plan') {
      return '正在判断本轮是查询数据、生成图表，还是直接回复。';
    }
    if (this.part().name === 'search') {
      return '正在检索与问题相关的数据表、字段、枚举值、业务知识、历史 SQL 和表关系。';
    }
    if (this.part().name === 'table_context') {
      return '正在确定最终使用的数据表，并整理生成 SQL 所需的字段、枚举、表关系和业务知识。';
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
    if (this.part().name === 'chart_analysis') {
      return '正在根据检索信息和当前查询结果选择分析维度、图表类型和配色。';
    }
    if (this.part().name === 'report_analysis') {
      return '正在根据当前查询结果撰写总体情况、分类分析和结论。';
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

  private taskPlanSummary(): string {
    const labels = this.taskPlanStepLabels();
    if (labels.length) {
      return `本轮将：${labels.join(' → ')}。`;
    }
    return '系统已完成本轮能力规划。';
  }

  private taskPlanItems(): string[] {
    const result = this.taskPlanResult();
    const items: string[] = [];
    const labels = this.taskPlanStepLabels();
    if (labels.length) {
      items.push(`执行步骤：${labels.join(' → ')}`);
    }
    const reason = this.text(result, 'reason');
    if (reason) {
      items.push(`规划说明：${reason}`);
    }
    const chartRequest = this.text(result, 'chart_request');
    if (chartRequest) {
      items.push(`出图要求：${chartRequest}`);
    }
    return items;
  }

  private taskPlanResult(): Record<string, unknown> | null {
    const output = this.outputObject();
    const nested = output?.['result'];
    if (nested && typeof nested === 'object') {
      return nested as Record<string, unknown>;
    }
    return output;
  }

  private taskPlanStepLabels(): string[] {
    const labels: Record<string, string> = {
      query: '查询数据',
      chart: '生成图表',
      chitchat: '对话回复',
      stop: '结束',
    };
    const result = this.taskPlanResult();
    const decisions = result?.['decisions'];
    if (Array.isArray(decisions) && decisions.length) {
      return decisions
        .map((item) => {
          const action =
            item && typeof item === 'object'
              ? String((item as Record<string, unknown>)['next'] || '').trim()
              : '';
          return labels[action] || '';
        })
        .filter((item) => Boolean(item) && item !== '结束');
    }
    const steps = result?.['steps'];
    const sequence = Array.isArray(steps)
      ? steps
      : result?.['next']
        ? [result['next']]
        : [];
    return sequence
      .map((item) => labels[String(item || '').trim()] || '')
      .filter((item) => Boolean(item) && item !== '结束');
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

  private chartAnalysisSummary(): string {
    const output = this.outputObject();
    const summary = this.text(output, 'summary');
    if (summary) {
      return summary;
    }
    if (output?.['status'] === 'ok') {
      return '已根据检索信息和当前查询结果选定分析维度与图表类型。';
    }
    return '正在根据上一轮查询结果规划图表。';
  }

  private reportAnalysisSummary(): string {
    const output = this.outputObject();
    const summary = this.text(output, 'summary');
    if (summary) {
      return summary;
    }
    if (output?.['status'] === 'ok') {
      return '已根据当前查询结果生成分析报告。';
    }
    return '正在根据当前查询结果生成分析报告。';
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

  private sqlSamplesFromRecord(record: Record<string, unknown>): SqlContextSample[] {
    const raw = record['sql_samples'];
    if (!Array.isArray(raw)) {
      return [];
    }
    return raw
      .map((item): SqlContextSample | null => {
        if (!item || typeof item !== 'object') {
          return null;
        }
        const sample = item as Record<string, unknown>;
        const name = typeof sample['name'] === 'string' ? sample['name'].trim() : '';
        const logic = typeof sample['logic'] === 'string' ? sample['logic'].trim() : '';
        const content = typeof sample['content'] === 'string' ? sample['content'].trim() : '';
        const relatedTables = this.stringArray(sample['related_tables']);
        if (!name && !logic && !content) {
          return null;
        }
        return {
          name: name || '未命名样例',
          logic,
          content,
          relatedTables,
        };
      })
      .filter((item): item is SqlContextSample => !!item);
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
