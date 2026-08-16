'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ApiError } from '@/lib/api/client';
import { createLiveStrategy, getBacktestConfig } from '@/lib/api/liveStrategies';
import type {
  BacktestConfig,
  LiveStrategyRiskConfig,
  ManualInterventionPolicy,
  OrderExecutionMode,
  PositionSizingMode,
} from '@/lib/types/liveStrategies';
import { formatCapital, formatTimeframe } from '@/lib/format';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';

const DEFAULT_RISK_CONFIG: LiveStrategyRiskConfig = {
  position_sizing_mode: 'fixed',
  position_sizing_value: 100000,
  max_position_per_market: 500000,
  order_execution_mode: 'limit_timeout',
  order_timeout_sec: 10,
  manual_intervention_policy: 'all_stop',
  daily_loss_limit_pct: -5,
  consecutive_loss_limit: 3,
};

type AmountField = 'position_sizing_value' | 'max_position_per_market';

export default function NewLiveStrategyPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sourceRunId = searchParams.get('source_run_id');

  const [config, setConfig] = useState<BacktestConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [riskConfig, setRiskConfig] = useState<LiveStrategyRiskConfig>(DEFAULT_RISK_CONFIG);
  const [amountInputs, setAmountInputs] = useState({
    position_sizing_value: String(DEFAULT_RISK_CONFIG.position_sizing_value),
    max_position_per_market: String(DEFAULT_RISK_CONFIG.max_position_per_market),
  });
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!sourceRunId) {
      setLoadError('source_run_id가 없습니다. 백테스트 상세 페이지에서 다시 시작하세요.');
      return;
    }
    getBacktestConfig(sourceRunId)
      .then(setConfig)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : '백테스트 설정을 불러오지 못했습니다.'));
  }, [sourceRunId]);

  function updateRiskConfig<K extends keyof LiveStrategyRiskConfig>(key: K, value: LiveStrategyRiskConfig[K]) {
    setRiskConfig((prev) => ({ ...prev, [key]: value }));
  }

  function updateAmountField(field: AmountField, raw: string) {
    const digits = raw.replace(/[^0-9]/g, '');
    setAmountInputs((prev) => ({ ...prev, [field]: digits }));
    updateRiskConfig(field, digits === '' ? 0 : Number(digits));
  }

  async function handleSubmit() {
    if (!config || !sourceRunId) return;
    setSubmitError(null);
    setSubmitting(true);
    try {
      await createLiveStrategy({
        source_run_id: sourceRunId,
        market: config.market,
        timeframe: config.timeframe,
        buy_conditions: config.buy_conditions,
        sell_conditions: config.sell_conditions,
        risk_config: riskConfig,
      });
      router.push('/live-strategies');
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '전략 생성 중 오류가 발생했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  if (loadError) return <p className="text-sm text-destructive">{loadError}</p>;
  if (!config) return <p className="text-sm text-muted-foreground">불러오는 중...</p>;

  return (
    <div className="max-w-4xl space-y-6">
      <div className="rounded-xl border p-4">
        <div className={SECTION_HEADER_CLASS}>대상 전략 (백테스트에서 그대로 승계)</div>
        <div className="p-3 text-sm">
          <p>{config.market} · {formatTimeframe(config.timeframe)}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            매수/매도 조건은 백테스트 상세 페이지의 조건과 100% 동일하게 적용됩니다.
          </p>
        </div>
      </div>

      <div className="space-y-4 rounded-xl border p-6 shadow-sm">
        <div className={SECTION_HEADER_CLASS}>자금관리</div>
        <div className="grid grid-cols-1 gap-4 p-3 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium">방식</label>
            <Select
              value={riskConfig.position_sizing_mode}
              onValueChange={(v) => v !== null && updateRiskConfig('position_sizing_mode', v as PositionSizingMode)}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="fixed">고정금액</SelectItem>
                <SelectItem value="percent">계좌잔고 비율(%)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">
              {riskConfig.position_sizing_mode === 'fixed' ? '금액(원)' : '비율(%)'}
            </label>
            <Input
              type="text" inputMode="numeric"
              value={formatCapital(amountInputs.position_sizing_value)}
              onChange={(e) => updateAmountField('position_sizing_value', e.target.value)}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">코인당 최대 포지션(원)</label>
            <Input
              type="text" inputMode="numeric"
              value={formatCapital(amountInputs.max_position_per_market)}
              onChange={(e) => updateAmountField('max_position_per_market', e.target.value)}
            />
          </div>
        </div>

        <div className={SECTION_HEADER_CLASS}>주문 실행</div>
        <div className="grid grid-cols-1 gap-4 p-3 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium">방식</label>
            <Select
              value={riskConfig.order_execution_mode}
              onValueChange={(v) => v !== null && updateRiskConfig('order_execution_mode', v as OrderExecutionMode)}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="market">시장가</SelectItem>
                <SelectItem value="limit">지정가</SelectItem>
                <SelectItem value="limit_timeout">지정가+타임아웃</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {riskConfig.order_execution_mode === 'limit_timeout' && (
            <div>
              <label className="mb-1.5 block text-sm font-medium">타임아웃(초)</label>
              <Input
                type="number" min={1}
                value={riskConfig.order_timeout_sec}
                onChange={(e) => updateRiskConfig('order_timeout_sec', Number(e.target.value))}
              />
            </div>
          )}
        </div>

        <div className={SECTION_HEADER_CLASS}>서킷브레이커 / 수동개입</div>
        <div className="grid grid-cols-1 gap-4 p-3 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-sm font-medium">일일 손실 한도(%)</label>
            <Input
              type="number" max={0}
              value={riskConfig.daily_loss_limit_pct}
              onChange={(e) => updateRiskConfig('daily_loss_limit_pct', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">연속 손실 한도(회)</label>
            <Input
              type="number" min={1}
              value={riskConfig.consecutive_loss_limit}
              onChange={(e) => updateRiskConfig('consecutive_loss_limit', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">수동개입 감지 시 정책</label>
            <Select
              value={riskConfig.manual_intervention_policy}
              onValueChange={(v) => v !== null && updateRiskConfig('manual_intervention_policy', v as ManualInterventionPolicy)}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all_stop">전체 정지</SelectItem>
                <SelectItem value="acknowledge_and_continue">인지 후 계속</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {submitError && <p className="text-sm text-destructive">{submitError}</p>}
        <Button onClick={handleSubmit} disabled={submitting}>
          {submitting ? '생성 중...' : '전략 만들기 (draft)'}
        </Button>
      </div>
    </div>
  );
}
