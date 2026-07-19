export type ComparisonOperator = '>' | '<' | '>=' | '<=' | '==';

export interface ConditionBlock {
  indicator: string;
  params: Record<string, number>;
  operator: ComparisonOperator;
  threshold: number;
}

export interface ConditionGroup {
  type: 'AND' | 'OR';
  conditions: Array<ConditionBlock | ConditionGroup>;
}
