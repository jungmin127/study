'use client';

import { Plus, TrendingUp, X } from 'lucide-react';
import type { ComparisonOperator, ConditionBlock, ConditionGroup } from '@/lib/types/strategy';
import type { IndicatorCatalogItem } from '@/lib/types/eda';
import { INPUT_CLASS, SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { OPERATOR_SYMBOLS, isConditionBlock, summarizeGroup } from '@/lib/condition-summary';
import { CATEGORY_DOT_COLOR, CATEGORY_ICON, CATEGORY_ORDER } from '@/lib/indicator-categories';
import InfoTooltip from '@/components/InfoTooltip';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';

const OPERATORS: { value: ComparisonOperator; label: string }[] = [
  { value: '>', label: '초과 (>)' },
  { value: '<', label: '미만 (<)' },
  { value: '>=', label: '이상 (≥)' },
  { value: '<=', label: '이하 (≤)' },
  { value: '==', label: '같음 (=)' },
];


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

// 오실레이터류는 흔히 쓰는 과매도/과매수 경계값, 나머지는 코인 시세 기반 추천값을 1차로 채워준다.
// 이용자가 직접 수정하는 것을 전제로 한 초기값일 뿐, 정답값이 아니다.
const OSCILLATOR_BOUNDS: Record<string, { low: number; high: number }> = {
  RSI: { low: 30, high: 70 },
  STOCH_K: { low: 20, high: 80 },
  STOCH_D: { low: 20, high: 80 },
  CCI: { low: -100, high: 100 },
  WILLIAMS_R: { low: -80, high: -20 },
};

const ZERO_CROSS_INDICATORS = new Set(['MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT']);
const PRICE_SCALE_INDICATORS = new Set([
  'SMA', 'EMA', 'WMA', 'BB_upper', 'BB_middle', 'BB_lower',
  'FIB_382', 'FIB_500', 'FIB_618', 'PIVOT_P', 'PIVOT_R1', 'PIVOT_S1',
]);

const POSITION_RELATIVE_DEFAULTS: Record<string, number> = {
  STOP_LOSS_PCT: -5,
  TAKE_PROFIT_PCT: 10,
  HOLDING_PERIOD_BARS: 20,
};

function recommendedThreshold(
  indicator: string,
  operator: ComparisonOperator,
  currentPrice: number | null,
): number {
  if (indicator in POSITION_RELATIVE_DEFAULTS) return POSITION_RELATIVE_DEFAULTS[indicator];
  if (PRICE_SCALE_INDICATORS.has(indicator)) return currentPrice ?? 0;
  if (ZERO_CROSS_INDICATORS.has(indicator)) return 0;
  if (indicator === 'ATR') return currentPrice ? Math.round(currentPrice * 0.01) : 1;

  const bounds = OSCILLATOR_BOUNDS[indicator];
  if (bounds) {
    if (operator === '<' || operator === '<=') return bounds.low;
    if (operator === '>' || operator === '>=') return bounds.high;
    return Math.round((bounds.low + bounds.high) / 2);
  }

  return 0; // OBV, VOLUME_SMA 등 코인마다 스케일이 제각각인 지표는 안전한 자리표시자만 채운다.
}

function createDefaultBlock(catalog: IndicatorCatalogItem[], currentPrice: number | null): ConditionBlock {
  const first = catalog.find((i) => i.value === 'RSI') ?? catalog[0];
  const indicator = first?.value ?? 'RSI';
  const operator: ComparisonOperator = '<';
  return {
    indicator,
    params: defaultParamsFor(first),
    operator,
    threshold: recommendedThreshold(indicator, operator, currentPrice),
  };
}

function createDefaultGroup(catalog: IndicatorCatalogItem[], currentPrice: number | null): ConditionGroup {
  return { type: 'AND', conditions: [createDefaultBlock(catalog, currentPrice)] };
}


// ── 조건 블록 에디터 ─────────────────────────────────────────────────────────
interface ConditionBlockEditorProps {
  block: ConditionBlock;
  catalog: IndicatorCatalogItem[];
  currentPrice: number | null;
  onChange: (updated: ConditionBlock) => void;
  onDelete: () => void;
}

function ConditionBlockEditor({ block, catalog, currentPrice, onChange, onDelete }: ConditionBlockEditorProps) {
  const categories = groupByCategory(catalog);
  const catalogItem = catalog.find((i) => i.value === block.indicator);
  const dotColor = catalogItem ? (CATEGORY_DOT_COLOR[catalogItem.category] ?? 'bg-slate-400') : 'bg-slate-400';
  const tooltip = catalogItem ? `${catalogItem.description}\n\n예시: ${catalogItem.example}` : '';

  function handleIndicatorChange(value: string) {
    const found = catalog.find((i) => i.value === value);
    const operator = found?.fixedOperator ?? block.operator;
    onChange({
      ...block,
      indicator: value,
      params: defaultParamsFor(found),
      operator,
      threshold: recommendedThreshold(value, operator, currentPrice),
    });
  }

  return (
    <div className="rounded-md border">
      <div className="flex items-center gap-2 rounded-t-md border-b bg-muted px-3 py-2">
        <span className={`h-2 w-2 shrink-0 rounded-full ${dotColor}`} />
        <Select value={block.indicator} onValueChange={(v) => { if (v) handleIndicatorChange(v); }}>
          <SelectTrigger className="h-auto flex-1 border-0 bg-transparent p-0 text-sm font-medium shadow-none focus:ring-0">
            <SelectValue>
              {(value: string | null) => catalog.find((i) => i.value === value)?.label ?? value ?? ''}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {categories.map((cat) => {
              const CategoryIcon = CATEGORY_ICON[cat.label] ?? TrendingUp;
              return (
                <SelectGroup key={cat.label}>
                  <SelectLabel className="flex items-center gap-1.5">
                    <CategoryIcon className="size-3.5" />
                    {cat.label}
                  </SelectLabel>
                  {cat.items.map((ind) => (
                    <SelectItem key={ind.value} value={ind.value}>
                      {ind.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              );
            })}
          </SelectContent>
        </Select>
        {tooltip && <InfoTooltip text={tooltip} />}
        <button
          type="button"
          onClick={onDelete}
          className="shrink-0 text-muted-foreground hover:text-red-500"
          aria-label="조건 삭제"
        >
          <X className="size-4" />
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
        <span className="rounded bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground">
          {block.indicator}
        </span>
        {catalogItem?.fixedOperator ? (
          <span className="flex h-7 shrink-0 items-center rounded border border-input bg-muted px-2 font-mono text-xs text-muted-foreground">
            {OPERATOR_SYMBOLS[catalogItem.fixedOperator]} 고정
          </span>
        ) : (
          <Select
            value={block.operator}
            onValueChange={(v) => { if (v) onChange({ ...block, operator: v as ComparisonOperator }); }}
          >
            <SelectTrigger className="h-7 w-auto border-input bg-background px-1 text-xs">
              <SelectValue>
                {(value: string | null) => OPERATORS.find((op) => op.value === value)?.label ?? value ?? ''}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {OPERATORS.map((op) => (
                <SelectItem key={op.value} value={op.value}>
                  {op.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
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
  currentPrice: number | null;
  onChange: (updated: ConditionGroup) => void;
  depth: number;
}

function ConditionGroupEditor({ group, catalog, currentPrice, onChange, depth }: ConditionGroupEditorProps) {
  function toggleOperator() {
    onChange({ ...group, type: group.type === 'AND' ? 'OR' : 'AND' });
  }

  function addBlock() {
    onChange({ ...group, conditions: [...group.conditions, createDefaultBlock(catalog, currentPrice)] });
  }

  function addGroup() {
    onChange({ ...group, conditions: [...group.conditions, createDefaultGroup(catalog, currentPrice)] });
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
              currentPrice={currentPrice}
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
                  aria-label="괄호 묶음 삭제"
                >
                  <X className="size-3.5" />
                </button>
              </div>
              <ConditionGroupEditor
                group={condition}
                catalog={catalog}
                currentPrice={currentPrice}
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
          className={`flex flex-1 items-center justify-center gap-1 ${INPUT_CLASS} bg-background text-xs font-medium hover:bg-muted`}
        >
          <Plus className="size-3.5" />
          조건 추가
        </button>
        {depth < 2 && (
          <button
            type="button"
            onClick={addGroup}
            className="flex flex-1 items-center justify-center gap-1 rounded-md border border-primary px-2 py-1.5 text-xs font-medium text-primary hover:bg-muted"
          >
            <Plus className="size-3.5" />
            괄호 묶음 추가
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
  currentPrice,
  onChange,
}: {
  label: string;
  group: ConditionGroup;
  catalog: IndicatorCatalogItem[];
  currentPrice: number | null;
  onChange: (updated: ConditionGroup) => void;
}) {
  return (
    <div>
      <div className={`rounded-t-md ${SECTION_HEADER_CLASS}`}>{label}</div>
      <div className="p-4">
        <ConditionGroupEditor group={group} catalog={catalog} currentPrice={currentPrice} onChange={onChange} depth={0} />
      </div>
      <div className="rounded-b-md border-t bg-muted px-4 py-2 text-xs">
        <span className="font-medium text-foreground">조건식: </span>
        <span className="font-mono text-muted-foreground">{summarizeGroup(group)}</span>
      </div>
    </div>
  );
}
