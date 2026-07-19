'use client';

import type { ComparisonOperator, ConditionBlock, ConditionGroup } from '@/lib/types/strategy';
import type { IndicatorCatalogItem } from '@/lib/types/eda';
import { INPUT_CLASS, SECTION_HEADER_CLASS } from '@/lib/ui-classes';

const CATEGORY_ORDER = ['추세', '오실레이터', '거래량', '시장 심리'];

const CATEGORY_DOT_COLOR: Record<string, string> = {
  추세: 'bg-blue-500',
  오실레이터: 'bg-violet-500',
  거래량: 'bg-teal-500',
  '시장 심리': 'bg-rose-500',
};

const OPERATORS: { value: ComparisonOperator; label: string }[] = [
  { value: '>', label: '초과 (>)' },
  { value: '<', label: '미만 (<)' },
  { value: '>=', label: '이상 (≥)' },
  { value: '<=', label: '이하 (≤)' },
  { value: '==', label: '같음 (=)' },
];

const OPERATOR_SYMBOLS: Record<ComparisonOperator, string> = {
  '>': '>',
  '<': '<',
  '>=': '≥',
  '<=': '≤',
  '==': '=',
};

function groupByCategory(catalog: IndicatorCatalogItem[]): { label: string; items: IndicatorCatalogItem[] }[] {
  return CATEGORY_ORDER.map((label) => ({
    label,
    items: catalog.filter((item) => item.category === label),
  })).filter((cat) => cat.items.length > 0);
}

function defaultParamsFor(item: IndicatorCatalogItem | undefined): Record<string, number> {
  if (!item) return {};
  return Object.fromEntries(item.params.map((p) => [p.key, p.default]));
}

function createDefaultBlock(catalog: IndicatorCatalogItem[]): ConditionBlock {
  const first = catalog.find((i) => i.value === 'RSI') ?? catalog[0];
  return {
    indicator: first?.value ?? 'RSI',
    params: defaultParamsFor(first),
    operator: '<',
    threshold: 30,
  };
}

function createDefaultGroup(catalog: IndicatorCatalogItem[]): ConditionGroup {
  return { type: 'AND', conditions: [createDefaultBlock(catalog)] };
}

function isConditionBlock(item: ConditionBlock | ConditionGroup): item is ConditionBlock {
  return 'indicator' in item;
}

function summarizeGroup(group: ConditionGroup): string {
  if (group.conditions.length === 0) return '(조건 없음)';
  const parts = group.conditions.map((c) =>
    isConditionBlock(c)
      ? `${c.indicator}${OPERATOR_SYMBOLS[c.operator]}${c.threshold}`
      : `(${summarizeGroup(c)})`
  );
  return parts.join(group.type === 'AND' ? ' and ' : ' or ');
}

// ── 조건 블록 에디터 ─────────────────────────────────────────────────────────
interface ConditionBlockEditorProps {
  block: ConditionBlock;
  catalog: IndicatorCatalogItem[];
  onChange: (updated: ConditionBlock) => void;
  onDelete: () => void;
}

function ConditionBlockEditor({ block, catalog, onChange, onDelete }: ConditionBlockEditorProps) {
  const categories = groupByCategory(catalog);
  const catalogItem = catalog.find((i) => i.value === block.indicator);
  const dotColor = catalogItem ? (CATEGORY_DOT_COLOR[catalogItem.category] ?? 'bg-slate-400') : 'bg-slate-400';
  const tooltip = catalogItem ? `${catalogItem.description}\n\n예시: ${catalogItem.example}` : '';

  function handleIndicatorChange(value: string) {
    const found = catalog.find((i) => i.value === value);
    onChange({ ...block, indicator: value, params: defaultParamsFor(found) });
  }

  return (
    <div className="overflow-hidden rounded-md border">
      <div className="flex items-center gap-2 border-b bg-slate-50 px-3 py-2 dark:bg-slate-800">
        <span className={`h-2 w-2 shrink-0 rounded-full ${dotColor}`} />
        <select
          className="flex-1 bg-transparent text-sm font-medium outline-none"
          value={block.indicator}
          onChange={(e) => handleIndicatorChange(e.target.value)}
        >
          {categories.map((cat) => (
            <optgroup key={cat.label} label={cat.label}>
              {cat.items.map((ind) => (
                <option key={ind.value} value={ind.value}>
                  {ind.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {tooltip && (
          <span
            className="shrink-0 cursor-help text-xs text-muted-foreground"
            title={tooltip}
            aria-label="지표 설명"
          >
            ⓘ
          </span>
        )}
        <button
          type="button"
          onClick={onDelete}
          className="shrink-0 text-muted-foreground hover:text-red-500"
          aria-label="조건 삭제"
        >
          ✕
        </button>
      </div>

      {Object.keys(block.params).length > 0 && (
        <div className="flex flex-wrap gap-3 border-b px-3 py-2">
          {Object.entries(block.params).map(([key, val]) => {
            const paramLabel = catalogItem?.params.find((p) => p.key === key)?.label ?? key;
            return (
              <div key={key} className="flex items-center gap-1.5">
                <span className="text-xs text-muted-foreground">{paramLabel}</span>
                <input
                  type="number"
                  value={val}
                  onChange={(e) =>
                    onChange({ ...block, params: { ...block.params, [key]: Number(e.target.value) } })
                  }
                  className="h-7 w-16 rounded border border-input bg-background px-1 text-center text-xs"
                />
              </div>
            );
          })}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-muted-foreground dark:bg-slate-800">
          {block.indicator}
        </span>
        <select
          value={block.operator}
          onChange={(e) => onChange({ ...block, operator: e.target.value as ComparisonOperator })}
          className="h-7 rounded border border-input bg-background px-1 text-xs"
        >
          {OPERATORS.map((op) => (
            <option key={op.value} value={op.value}>
              {op.label}
            </option>
          ))}
        </select>
        <input
          type="number"
          value={block.threshold}
          onChange={(e) => onChange({ ...block, threshold: Number(e.target.value) })}
          className="h-7 flex-1 rounded border border-input bg-background px-2 text-xs"
        />
        <span className="shrink-0 rounded-md bg-primary px-2 py-0.5 font-mono text-xs font-semibold text-primary-foreground">
          {OPERATOR_SYMBOLS[block.operator]} {block.threshold}
        </span>
      </div>
    </div>
  );
}

// ── 조건 그룹 에디터 ─────────────────────────────────────────────────────────
interface ConditionGroupEditorProps {
  group: ConditionGroup;
  catalog: IndicatorCatalogItem[];
  onChange: (updated: ConditionGroup) => void;
  depth: number;
}

function ConditionGroupEditor({ group, catalog, onChange, depth }: ConditionGroupEditorProps) {
  function toggleOperator() {
    onChange({ ...group, type: group.type === 'AND' ? 'OR' : 'AND' });
  }

  function addBlock() {
    onChange({ ...group, conditions: [...group.conditions, createDefaultBlock(catalog)] });
  }

  function addGroup() {
    onChange({ ...group, conditions: [...group.conditions, createDefaultGroup(catalog)] });
  }

  function updateCondition(index: number, updated: ConditionBlock | ConditionGroup) {
    const next = [...group.conditions];
    next[index] = updated;
    onChange({ ...group, conditions: next });
  }

  function deleteCondition(index: number) {
    onChange({ ...group, conditions: group.conditions.filter((_, i) => i !== index) });
  }

  return (
    <div className={depth > 0 ? 'ml-3 space-y-2 border-l-2 border-primary pl-3' : 'space-y-2'}>
      {group.conditions.length === 0 && (
        <div className="rounded-md border-2 border-dashed py-4 text-center">
          <p className="text-xs font-medium text-muted-foreground">조건이 없습니다</p>
          <p className="mt-0.5 text-xs text-muted-foreground">아래 버튼으로 조건을 추가하세요</p>
        </div>
      )}

      {group.conditions.map((condition, index) => (
        <div key={index}>
          {index > 0 && (
            <div className="flex items-center gap-2 py-1">
              <div className="h-px flex-1 bg-border" />
              <button
                type="button"
                onClick={toggleOperator}
                className={
                  group.type === 'AND'
                    ? 'rounded-full bg-primary px-3 py-0.5 text-xs font-bold text-primary-foreground hover:opacity-90'
                    : 'rounded-full bg-amber-500/15 px-3 py-0.5 text-xs font-bold text-amber-700 hover:bg-amber-500/25 dark:text-amber-400'
                }
              >
                {group.type === 'AND' ? '그리고 (AND)' : '또는 (OR)'}
              </button>
              <div className="h-px flex-1 bg-border" />
            </div>
          )}

          {isConditionBlock(condition) ? (
            <ConditionBlockEditor
              block={condition}
              catalog={catalog}
              onChange={(updated) => updateCondition(index, updated)}
              onDelete={() => deleteCondition(index)}
            />
          ) : (
            <div className="space-y-2 rounded-md border-2 border-dashed p-2.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-primary">
                  ( ) 괄호 묶음 — 내부에서 별도 AND/OR 적용
                </span>
                <button
                  type="button"
                  onClick={() => deleteCondition(index)}
                  className="text-xs text-muted-foreground hover:text-red-500"
                >
                  ✕
                </button>
              </div>
              <ConditionGroupEditor
                group={condition}
                catalog={catalog}
                onChange={(updated) => updateCondition(index, updated)}
                depth={depth + 1}
              />
            </div>
          )}
        </div>
      ))}

      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={addBlock}
          className={`flex-1 ${INPUT_CLASS} bg-background text-xs font-medium hover:bg-slate-50 dark:hover:bg-slate-800`}
        >
          + 조건 추가
        </button>
        {depth < 2 && (
          <button
            type="button"
            onClick={addGroup}
            className="flex-1 rounded-md border border-primary px-2 py-1.5 text-xs font-medium text-primary hover:bg-slate-50 dark:hover:bg-slate-800"
          >
            + 괄호 묶음 추가
          </button>
        )}
      </div>
    </div>
  );
}

export default function StrategyConditionBuilder({
  label,
  group,
  catalog,
  onChange,
}: {
  label: string;
  group: ConditionGroup;
  catalog: IndicatorCatalogItem[];
  onChange: (updated: ConditionGroup) => void;
}) {
  return (
    <div>
      <div className={SECTION_HEADER_CLASS}>{label}</div>
      <div className="p-4">
        <ConditionGroupEditor group={group} catalog={catalog} onChange={onChange} depth={0} />
      </div>
      <div className="border-t bg-slate-50 px-4 py-2 text-xs dark:bg-slate-800">
        <span className="font-medium text-foreground">조건식: </span>
        <span className="font-mono text-muted-foreground">{summarizeGroup(group)}</span>
      </div>
    </div>
  );
}
