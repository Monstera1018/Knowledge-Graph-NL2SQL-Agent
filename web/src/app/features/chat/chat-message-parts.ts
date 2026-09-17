import type { ArtifactPart, DataPart, MessagePart, TextPart, ToolPart } from '../../core/models/chat.models';
import { mergeToolTiming } from './tool-duration';

const PART_ORDER = {
  workflow: 0,
  tool: 10,
  data_plan: 18,
  text: 20,
  data_table: 40,
  data_diagnostic: 50,
  artifact_thinking: 55,
  artifact_sql_rationale: 58,
  artifact_sql: 60,
  text_query: 70,
  artifact: 80,
  error: 90,
} as const;

const QUERY_RESULT_TEXT_RE = /(?:##|###)\s*[✅🔎📊]?\s*(?:SQL 合法性检查|查询结果)/;

const TABLE_CONTEXT_TOOL_NAMES = new Set([
  'table_selector',
  'route_after_table_selection',
  'format_no_table_response',
  'load_sql_schema_context',
]);

const PLAN_TOOL_NAMES = new Set(['planner', 'parse_plan']);

const CHART_TOOL_NAMES = new Set([
  'prepare_chart_planner_input',
  'chart_planner',
  'parse_chart_spec',
  'format_no_chart_snapshot_response',
]);

const REPORT_TOOL_NAMES = new Set([
  'prepare_report_planner_input',
  'report_planner',
  'parse_report_spec',
]);

type TableDetail = {
  name: string;
  comment?: string;
  description?: string;
  [key: string]: unknown;
};

export function isQueryResultText(part: TextPart): boolean {
  return QUERY_RESULT_TEXT_RE.test(part.text);
}

export function partOrderKey(part: MessagePart): number {
  if (part.kind === 'text') {
    return isQueryResultText(part) ? PART_ORDER.text_query : PART_ORDER.text;
  }
  if (part.kind === 'workflow') {
    return PART_ORDER.workflow;
  }
  if (part.kind === 'tool') {
    return PART_ORDER.tool;
  }
  if (part.kind === 'error') {
    return PART_ORDER.error;
  }
  if (part.kind === 'data') {
    const dataPart = part as DataPart;
    const kind = dataPart.payload['kind'];
    if (kind === 'schema_plan') {
      return PART_ORDER.data_plan;
    }
    if (kind === 'sql_diagnostic') {
      return PART_ORDER.data_diagnostic;
    }
    return PART_ORDER.data_table;
  }
  if (part.kind === 'artifact') {
    const artifact = part as ArtifactPart;
    const language = artifact.language.trim().toLowerCase();
    if (artifact.id.includes('sql-rationale') || artifact.title === '查询说明') {
      return PART_ORDER.artifact_sql_rationale;
    }
    if (language === 'sql') {
      return PART_ORDER.artifact_sql;
    }
    if (language === 'thinking') {
      return PART_ORDER.artifact_thinking;
    }
    return PART_ORDER.artifact;
  }
  return 100;
}

export function shouldShowDataPart(part: DataPart, streaming = false): boolean {
  const kind = part.payload['kind'];
  if (kind === 'schema_plan') {
    return false;
  }
  if (kind !== 'sql_diagnostic') {
    return true;
  }
  return part.payload['resolved'] !== true;
}

export function shouldShowArtifactPart(part: ArtifactPart): boolean {
  const language = part.language.trim().toLowerCase();
  if (language === 'thinking') {
    return Boolean(part.content.trim());
  }
  return true;
}

export function shouldShowTextPart(part: TextPart, streaming = false): boolean {
  const text = part.text.trim();
  if (!text) {
    return false;
  }
  if (
    streaming &&
    /##\s*❌\s*SQL 合法性检查/.test(text) &&
    !/##\s*✅\s*SQL 合法性检查/.test(text)
  ) {
    return false;
  }
  return true;
}

export function sortVisibleParts(parts: readonly MessagePart[]): MessagePart[] {
  return parts.slice().sort((left, right) => partOrderKey(left) - partOrderKey(right));
}

export function mergeDisplayToolParts(parts: readonly MessagePart[]): MessagePart[] {
  const merged: MessagePart[] = [];
  const tableContextParts: ToolPart[] = [];
  let tableContextIndex = -1;
  const planParts: ToolPart[] = [];
  let planIndex = -1;
  const chartParts: ToolPart[] = [];
  let chartIndex = -1;
  const reportParts: ToolPart[] = [];
  let reportIndex = -1;

  for (const part of parts) {
    if (part.kind === 'tool' && TABLE_CONTEXT_TOOL_NAMES.has(part.name)) {
      tableContextParts.push(part);
      const tableContext = buildTableContextPart(tableContextParts);
      if (tableContextIndex < 0) {
        tableContextIndex = merged.length;
        merged.push(tableContext);
      } else {
        merged[tableContextIndex] = tableContext;
      }
      continue;
    }
    if (part.kind === 'tool' && PLAN_TOOL_NAMES.has(part.name)) {
      planParts.push(part);
      const planPart = buildTaskPlanPart(planParts);
      if (planIndex < 0) {
        planIndex = merged.length;
        merged.push(planPart);
      } else {
        merged[planIndex] = planPart;
      }
      continue;
    }
    if (part.kind === 'tool' && CHART_TOOL_NAMES.has(part.name)) {
      chartParts.push(part);
      const chartPart = buildChartAnalysisPart(chartParts);
      if (chartIndex < 0) {
        chartIndex = merged.length;
        merged.push(chartPart);
      } else {
        merged[chartIndex] = chartPart;
      }
      continue;
    }
    if (part.kind === 'tool' && REPORT_TOOL_NAMES.has(part.name)) {
      reportParts.push(part);
      const reportPart = buildReportAnalysisPart(reportParts);
      if (reportIndex < 0) {
        reportIndex = merged.length;
        merged.push(reportPart);
      } else {
        merged[reportIndex] = reportPart;
      }
      continue;
    }
    merged.push(part);
  }

  return merged;
}

function buildTaskPlanPart(parts: readonly ToolPart[]): ToolPart {
  const first = parts[0];
  const phase = parts.some((part) => part.phase === 'call') ? 'call' : 'result';
  const parsed = findToolPart(parts, 'parse_plan');
  const planner = findToolPart(parts, 'planner');
  const parsedOutput = outputRecord(parsed);
  const plannerOutput = outputRecord(planner);
  const result =
    recordValue(parsedOutput, 'result') ??
    recordValue(plannerOutput, 'result') ??
    (parsedOutput && Object.keys(parsedOutput).length ? parsedOutput : null) ??
    (plannerOutput && Object.keys(plannerOutput).length ? plannerOutput : null);

  return {
    kind: 'tool',
    id: `${first.id}-task-plan`,
    name: 'task_plan',
    phase,
    title: '规划步骤',
    input: first.input ?? null,
    ...mergeToolTiming(parts),
    output: {
      stage: 'task_plan',
      status: phase === 'call' ? 'running' : 'ok',
      result,
      summary: stringValue(parsedOutput, 'summary') || stringValue(plannerOutput, 'summary') || '',
      merged_stages: parts.map((part) => part.name),
    },
  };
}

function buildTableContextPart(parts: readonly ToolPart[]): ToolPart {
  const first = parts[0];
  const selector = findToolPart(parts, 'table_selector');
  const route = findToolPart(parts, 'route_after_table_selection');
  const noTable = findToolPart(parts, 'format_no_table_response');
  const schema = findToolPart(parts, 'load_sql_schema_context');
  const phase = parts.some((part) => part.phase === 'call') ? 'call' : 'result';

  const selectorOutput = outputRecord(selector);
  const routeOutput = outputRecord(route);
  const noTableOutput = outputRecord(noTable);
  const schemaOutput = outputRecord(schema);
  const schemaSections = arrayValue(schemaOutput, 'sections');
  const result = enrichSelectionResult(
    recordValue(selectorOutput, 'result') ?? recordValue(routeOutput, 'result') ?? null,
    schemaSections,
  );

  return {
    kind: 'tool',
    id: `${first.id}-table-context`,
    name: 'table_context',
    phase,
    title: '确定数据表与表结构',
    input: first.input ?? null,
    ...mergeToolTiming(parts),
    output: {
      stage: 'table_context',
      status: phase === 'call' ? 'running' : 'ok',
      result,
      sections: schemaSections,
      summary:
        stringValue(schemaOutput, 'summary') ||
        stringValue(noTableOutput, 'summary') ||
        stringValue(noTableOutput, 'result'),
      merged_stages: parts.map((part) => part.name),
    },
  };
}

function buildChartAnalysisPart(parts: readonly ToolPart[]): ToolPart {
  const first = parts[0];
  const phase = parts.some((part) => part.phase === 'call') ? 'call' : 'result';
  const planner = findToolPart(parts, 'chart_planner');
  const parsed = findToolPart(parts, 'parse_chart_spec');
  const missing = findToolPart(parts, 'format_no_chart_snapshot_response');
  const parsedOutput = outputRecord(parsed);
  const missingOutput = outputRecord(missing);
  const plannerOutput = outputRecord(planner);

  return {
    kind: 'tool',
    id: `${first.id}-chart-analysis`,
    name: 'chart_analysis',
    phase,
    title: '分析图表',
    input: first.input ?? null,
    ...mergeToolTiming(parts),
    output: {
      stage: 'chart_analysis',
      status: phase === 'call' ? 'running' : 'ok',
      result: recordValue(parsedOutput, 'result') ?? recordValue(plannerOutput, 'result') ?? null,
      summary:
        stringValue(parsedOutput, 'summary') ||
        stringValue(missingOutput, 'summary') ||
        stringValue(missingOutput, 'result') ||
        '',
      merged_stages: parts.map((part) => part.name),
    },
  };
}

function buildReportAnalysisPart(parts: readonly ToolPart[]): ToolPart {
  const first = parts[0];
  const phase = parts.some((part) => part.phase === 'call') ? 'call' : 'result';
  const planner = findToolPart(parts, 'report_planner');
  const parsed = findToolPart(parts, 'parse_report_spec');
  const parsedOutput = outputRecord(parsed);
  const plannerOutput = outputRecord(planner);

  return {
    kind: 'tool',
    id: `${first.id}-report-analysis`,
    name: 'report_analysis',
    phase,
    title: '分析报告',
    input: first.input ?? null,
    ...mergeToolTiming(parts),
    output: {
      stage: 'report_analysis',
      status: phase === 'call' ? 'running' : 'ok',
      result: recordValue(parsedOutput, 'result') ?? recordValue(plannerOutput, 'result') ?? null,
      summary: stringValue(parsedOutput, 'summary') || stringValue(plannerOutput, 'summary') || '',
      merged_stages: parts.map((part) => part.name),
    },
  };
}

function enrichSelectionResult(result: unknown, sections: readonly unknown[]): unknown {
  if (!result || typeof result !== 'object') {
    return result;
  }

  const record = result as Record<string, unknown>;
  const details = normalizeTableDetails(record['table_details']);
  if (!details.length) {
    return result;
  }

  const metaByName = extractTableMetaFromSections(sections);
  if (!metaByName.size) {
    return result;
  }

  let changed = false;
  const enriched = details.map((detail) => {
    const meta = metaByName.get(detail.name.toLowerCase());
    if (!meta) {
      return detail;
    }
    const comment = stringOrEmpty(detail.comment) || meta.comment;
    const description = stringOrEmpty(detail.description) || meta.description;
    if (comment !== stringOrEmpty(detail.comment) || description !== stringOrEmpty(detail.description)) {
      changed = true;
    }
    return {
      ...detail,
      comment,
      description,
    };
  });

  if (!changed) {
    return result;
  }

  return {
    ...record,
    table_details: enriched,
    selected_table_details: enriched,
  };
}

function normalizeTableDetails(value: unknown): TableDetail[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item): TableDetail | null => {
      if (!item || typeof item !== 'object') {
        return null;
      }
      const record = item as Record<string, unknown>;
      const name = stringOrEmpty(record['name']);
      if (!name) {
        return null;
      }
      return {
        ...record,
        name,
        comment: stringOrEmpty(record['comment']),
        description: stringOrEmpty(record['description']),
      };
    })
    .filter((item): item is TableDetail => !!item);
}

function extractTableMetaFromSections(sections: readonly unknown[]): Map<string, Pick<TableDetail, 'comment' | 'description'>> {
  const metaByName = new Map<string, Pick<TableDetail, 'comment' | 'description'>>();

  for (const section of sections) {
    if (!section || typeof section !== 'object') {
      continue;
    }
    const record = section as Record<string, unknown>;
    const title = normalizeTitle(stringOrEmpty(record['title']));
    if (title !== '库表结构(供生成SQL)' && title !== '数据表') {
      continue;
    }
    const items = arrayValue(record, 'items').map((item) => stringOrEmpty(item)).filter(Boolean);
    let current: TableDetail | null = null;
    for (const item of items) {
      const tableName = parseTableNameItem(item);
      if (tableName) {
        current = { name: tableName, comment: '', description: '' };
        metaByName.set(tableName.toLowerCase(), current);
        continue;
      }
      if (!current) {
        continue;
      }
      const comment = parsePrefixedValue(item, '说明');
      if (comment && !current.comment) {
        current.comment = comment;
        continue;
      }
      const description = parsePrefixedValue(item, '摘要');
      if (description && !current.description) {
        current.description = description;
      }
    }
  }

  return metaByName;
}

function parseTableNameItem(item: string): string {
  const cleaned = item.replace(/\*\*/g, '').replace(/`/g, '').trim();
  return /^[A-Za-z][A-Za-z0-9_.$]*$/.test(cleaned) && cleaned.includes('_') ? cleaned : '';
}

function parsePrefixedValue(item: string, label: string): string {
  const cleaned = item.replace(/\*\*/g, '').replace(/`/g, '').trim();
  const match = new RegExp(`^${label}[:：]\\s*(.*)$`).exec(cleaned);
  return match?.[1]?.trim() ?? '';
}

function normalizeTitle(title: string): string {
  return title
    .trim()
    .replace(/\s+/g, '')
    .replace(/（/g, '(')
    .replace(/）/g, ')');
}

function findToolPart(parts: readonly ToolPart[], name: string): ToolPart | undefined {
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    if (parts[index].name === name) {
      return parts[index];
    }
  }
  return undefined;
}

function outputRecord(part: ToolPart | undefined): Record<string, unknown> | null {
  if (!part?.output || typeof part.output !== 'object') {
    return null;
  }
  return part.output as Record<string, unknown>;
}

function recordValue(record: Record<string, unknown> | null, key: string): unknown {
  return record?.[key];
}

function arrayValue(record: Record<string, unknown> | null, key: string): unknown[] {
  const value = record?.[key];
  return Array.isArray(value) ? value : [];
}

function stringValue(record: Record<string, unknown> | null, key: string): string {
  const value = record?.[key];
  return typeof value === 'string' ? value : '';
}

function stringOrEmpty(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function isHorizontalRuleChunk(chunk: string): boolean {
  return /^-{3,}$/.test(chunk.trim());
}

/** Validation block before query results within the same text part. */
export function reorderQueryMarkdown(text: string): string {
  const chunks = text.split(/\n\n---\n\n/);
  if (chunks.length < 2) {
    return text;
  }

  const validation: string[] = [];
  const results: string[] = [];
  const others: string[] = [];

  for (const chunk of chunks) {
    const trimmed = chunk.trim();
    if (!trimmed || isHorizontalRuleChunk(trimmed)) {
      continue;
    }
    if (/SQL 合法性检查/.test(trimmed)) {
      validation.push(trimmed);
    } else if (/查询结果/.test(trimmed)) {
      results.push(trimmed);
    } else {
      others.push(trimmed);
    }
  }

  if (!validation.length && !results.length) {
    return text;
  }

  const preferredValidation =
    validation.find((chunk) => /##\s*✅\s*SQL 合法性检查/.test(chunk)) ??
    validation[validation.length - 1];

  return [...others, preferredValidation, ...results].filter(Boolean).join('\n\n');
}
