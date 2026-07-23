import type { ComparisonOperator, ConditionBlock, ConditionGroup } from '@/lib/types/strategy';

export const OPERATOR_SYMBOLS: Record<ComparisonOperator, string> = {
  '>': '>',
  '<': '<',
  '>=': '≥',
  '<=': '≤',
  '==': '=',
};

export function isConditionBlock(item: ConditionBlock | ConditionGroup): item is ConditionBlock {
  return 'indicator' in item;
}

export function summarizeGroup(group: ConditionGroup): string {
  if (group.conditions.length === 0) return '(조건 없음)';
  const parts = group.conditions.map((c) =>
    isConditionBlock(c)
      ? `${c.indicator}${OPERATOR_SYMBOLS[c.operator]}${c.threshold}`
      : `(${summarizeGroup(c)})`
  );
  return parts.join(group.type === 'AND' ? ' and ' : ' or ');
}
