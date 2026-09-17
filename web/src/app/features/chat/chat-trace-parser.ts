export type TraceSectionTier = 'summary' | 'detail' | 'debug';

export interface TraceSection {
  title: string;
  content: string;
  language: string;
  tier: TraceSectionTier;
}

export interface TraceStep {
  id: string;
  node: string;
  title: string;
  emoji: string;
  label: string;
  status: 'done' | 'active' | 'error';
  sections: TraceSection[];
  summarySections: TraceSection[];
  detailSections: TraceSection[];
  debugSections: TraceSection[];
  summary: string;
  emphasis: 'default' | 'sql' | 'check' | 'error';
}

interface RawTraceStep {
  v?: number;
  node?: string;
  label?: string;
  status?: string;
  sections?: Array<Partial<TraceSection>>;
}

const EMOJI_LEADING_RE =
  /^(\p{Extended_Pictographic}\uFE0F?(?:\u200D\p{Extended_Pictographic}\uFE0F?)*)\s*(.*)$/u;

const SQL_STEP_NODES = new Set(['generate_sql']);
const CHECK_STEP_NODES = new Set(['validate_sql']);

const SUMMARY_SECTION_TITLES = new Set([
  '召回依据',
  '命中统计',
  '检索问句',
  '校验结论',
  '执行结论',
  '错误',
  '执行错误',
  '停止原因',
  '结论',
  '说明',
]);

const DEBUG_SECTION_TITLES = new Set(['System 提示词', '模型输出', '模型原始输出']);

export function parseTraceSteps(content: string): TraceStep[] {
  const text = (content || '').trim();
  if (!text) {
    return [];
  }

  let rawSteps: RawTraceStep[];
  try {
    const parsed = JSON.parse(text) as unknown;
    if (!Array.isArray(parsed)) {
      return [];
    }
    rawSteps = parsed as RawTraceStep[];
  } catch {
    return [];
  }

  return rawSteps
    .map((step, index) => buildTraceStep(step, index))
    .filter((step): step is TraceStep => step !== null);
}

function buildTraceStep(raw: RawTraceStep, index: number): TraceStep | null {
  const node = (raw.node || `step_${index}`).trim();
  const title = (raw.label || node).trim();
  const sections = (raw.sections || [])
    .map((section) => normalizeSection(section))
    .filter((section): section is TraceSection => section !== null);

  if (!sections.length) {
    return null;
  }

  const parsed = splitTitleEmoji(title);
  const summarySections = sections.filter((section) => section.tier === 'summary');
  const detailSections = sections.filter((section) => section.tier === 'detail');
  const debugSections = sections.filter((section) => section.tier === 'debug');

  return {
    id: `trace-step-${node}-${index}`,
    node,
    title,
    emoji: parsed.emoji,
    label: parsed.label,
    status: normalizeStatus(raw.status),
    sections,
    summarySections,
    detailSections,
    debugSections,
    summary: summarizeSections(summarySections.length ? summarySections : sections),
    emphasis: stepEmphasis(node, parsed.label, sections, raw.status),
  };
}

function normalizeSection(raw: Partial<TraceSection>): TraceSection | null {
  const title = (raw.title || '').trim();
  const content = (raw.content || '').trim();
  if (!title || !content) {
    return null;
  }
  return {
    title,
    content,
    language: (raw.language || 'markdown').trim().toLowerCase() || 'markdown',
    tier: classifySectionTier(title),
  };
}

export function classifySectionTier(title: string): TraceSectionTier {
  const normalized = title.trim();
  if (DEBUG_SECTION_TITLES.has(normalized) || normalized.startsWith('User 输入')) {
    return 'debug';
  }
  if (SUMMARY_SECTION_TITLES.has(normalized)) {
    return 'summary';
  }
  return 'detail';
}

function splitTitleEmoji(title: string): { emoji: string; label: string } {
  const match = title.match(EMOJI_LEADING_RE);
  if (!match) {
    return { emoji: '•', label: title };
  }
  return {
    emoji: match[1],
    label: match[2].trim() || title,
  };
}

function normalizeStatus(status: string | undefined): TraceStep['status'] {
  const text = (status || '').trim().toLowerCase();
  if (text === 'error') {
    return 'error';
  }
  if (text === 'active' || text === 'running') {
    return 'active';
  }
  return 'done';
}

function stepEmphasis(
  node: string,
  label: string,
  sections: readonly TraceSection[],
  status: string | undefined,
): TraceStep['emphasis'] {
  if (normalizeStatus(status) === 'error') {
    return 'error';
  }
  if (CHECK_STEP_NODES.has(node) || label.includes('SQL 合法性检查') || label.includes('SQL 校验')) {
    return 'check';
  }
  if (SQL_STEP_NODES.has(node) || label.includes('SQL 生成')) {
    return 'sql';
  }
  return 'default';
}

function summarizeSections(sections: readonly TraceSection[]): string {
  for (const section of sections) {
    for (const rawLine of section.content.split('\n')) {
      const line = rawLine.trim();
      if (!line || line.startsWith('#')) {
        continue;
      }
      const cleaned = line
        .replace(/^[-*]\s+/, '')
        .replace(/^\*\*(.+?)\*\*/, '$1')
        .replace(/\*\*/g, '')
        .replace(/`/g, '');
      if (cleaned) {
        return cleaned.length > 96 ? `${cleaned.slice(0, 96)}…` : cleaned;
      }
    }
  }
  return sections[0]?.title || '已完成';
}

export function sectionMarkdown(section: TraceSection): string {
  if (section.language === 'sql') {
    return `\`\`\`sql\n${section.content}\n\`\`\``;
  }
  if (section.language === 'json') {
    return `\`\`\`json\n${section.content}\n\`\`\``;
  }
  return section.content;
}

export function defaultExpandedStepIds(steps: readonly TraceStep[], streaming: boolean): Set<string> {
  if (!steps.length) {
    return new Set();
  }

  const expanded = new Set<string>();
  if (streaming) {
    expanded.add(steps[steps.length - 1].id);
  }
  return expanded;
}
