'use client';

import type { ComparisonOperator, ConditionBlock, ConditionGroup } from '@/lib/types/strategy';
import { INPUT_CLASS, SECTION_HEADER_CLASS } from '@/lib/ui-classes';

interface IndicatorDef {
  value: string;
  label: string;
  defaultParams: Record<string, number>;
}

interface IndicatorCategory {
  label: string;
  items: IndicatorDef[];
}

const INDICATOR_CATEGORIES: IndicatorCategory[] = [
  {
    label: '추세',
    items: [
      { value: 'SMA', label: 'SMA (단순 이동평균)', defaultParams: { period: 14 } },
      { value: 'EMA', label: 'EMA (지수 이동평균)', defaultParams: { period: 14 } },
      { value: 'WMA', label: 'WMA (가중 이동평균)', defaultParams: { period: 14 } },
    ],
  },
  {
    label: '오실레이터',
    items: [
      { value: 'RSI', label: 'RSI', defaultParams: { period: 14 } },
      { value: 'MACD_line', label: 'MACD Line', defaultParams: { fast: 12, slow: 26 } },
      { value: 'MACD_signal', label: 'MACD Signal', defaultParams: { fast: 12, slow: 26, signal: 9 } },
      { value: 'STOCH_K', label: '스토캐스틱 %K', defaultParams: { k_period: 14, d_period: 3 } },
      { value: 'STOCH_D', label: '스토캐스틱 %D', defaultParams: { k_period: 14, d_period: 3 } },
      { value: 'CCI', label: 'CCI', defaultParams: { period: 20 } },
      { value: 'WILLIAMS_R', label: 'Williams %R', defaultParams: { period: 14 } },
      { value: 'BB_upper', label: 'BB 상단', defaultParams: { period: 20 } },
      { value: 'BB_lower', label: 'BB 하단', defaultParams: { period: 20 } },
      { value: 'BB_middle', label: 'BB 중간선', defaultParams: { period: 20 } },
      { value: 'ATR', label: 'ATR', defaultParams: { period: 14 } },
    ],
  },
  {
    label: '거래량',
    items: [
      { value: 'OBV', label: 'OBV', defaultParams: {} },
      { value: 'VOLUME_SMA', label: '거래량 SMA', defaultParams: { period: 20 } },
    ],
  },
  {
    label: '시장 심리',
    items: [{ value: 'FEAR_GREED_CMC', label: 'CMC 공포/탐욕 지수', defaultParams: {} }],
  },
];

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

const PARAM_LABELS: Record<string, string> = {
  period: '기간',
  fast: '단기',
  slow: '장기',
  signal: '시그널',
  k_period: 'K기간',
  d_period: 'D기간',
};

const ALL_INDICATORS = INDICATOR_CATEGORIES.flatMap((cat) => cat.items);

function createDefaultBlock(): ConditionBlock {
  return { indicator: 'RSI', params: { period: 14 }, operator: '<', threshold: 30 };
}

function createDefaultGroup(): ConditionGroup {
  return { type: 'AND', conditions: [createDefaultBlock()] };
}

function isConditionBlock(item: ConditionBlock | ConditionGroup): item is ConditionBlock {
  return 'indicator' in item;
}

function ConditionBlockEditor({
  block,
  onChange,
  onDelete,
}: {
  block: ConditionBlock;
  onChange: (updated: ConditionBlock) => void;
  onDelete: () => void;
}) {
  const category = INDICATOR_CATEGORIES.find((cat) => cat.items.some((i) => i.value === block.indicator));
  const dotColor = category ? (CATEGORY_DOT_COLOR[category.label] ?? 'bg-slate-400') : 'bg-slate-400';
  const operatorSymbol = { '>': '>', '<': '<', '>=': '≥', '<=': '≤', '==': '=' }[block.operator] ?? block.operator;

  function handleIndicatorChange(value: string) {
    const found = ALL_INDICATORS.find((i) => i.value === value);
    onChange({ ...block, indicator: value, params: found?.defaultParams ?? {} });
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
          {INDICATOR_CATEGORIES.map((cat) => (
            <optgroup key={cat.label} label={cat.label}>
              {cat.items.map((ind) => (
                <option key={ind.value} value={ind.value}>
                  {ind.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
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
          {Object.entries(block.params).map(([key, val]) => (
            <div key={key} className="flex items-center gap-1.5">
              <span className="text-xs text-muted-foreground">{PARAM_LABELS[key] ?? key}</span>
              <input
                type="number"
                value={val}
                onChange={(e) =>
                  onChange({ ...block, params: { ...block.params, [key]: Number(e.target.value) } })
                }
                className="h-7 w-16 rounded border border-input bg-background px-1 text-center text-xs"
              />
            </div>
          ))}
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
          {operatorSymbol} {block.threshold}
        </span>
      </div>
    </div>
  );
}

function ConditionGroupEditor({
  group,
  onChange,
  depth,
}: {
  group: ConditionGroup;
  onChange: (updated: ConditionGroup) => void;
  depth: number;
}) {
  function toggleOperator() {
    onChange({ ...group, type: group.type === 'AND' ? 'OR' : 'AND' });
  }

  function addBlock() {
    onChange({ ...group, conditions: [...group.conditions, createDefaultBlock()] });
  }

  function addGroup() {
    onChange({ ...group, conditions: [...group.conditions, createDefaultGroup()] });
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
  onChange,
}: {
  label: string;
  group: ConditionGroup;
  onChange: (updated: ConditionGroup) => void;
}) {
  return (
    <div>
      <div className={SECTION_HEADER_CLASS}>{label}</div>
      <div className="p-4">
        <ConditionGroupEditor group={group} onChange={onChange} depth={0} />
      </div>
    </div>
  );
}
