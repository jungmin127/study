'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';

const CANDLE_UNITS = ['15분', '30분', '1시간', '1일'];

const INPUT_CLASS =
  'h-10 rounded-md border border-input bg-background px-3 text-sm shadow-sm outline-none focus:ring-2 focus:ring-ring';

const SELECT_CLASS = `${INPUT_CLASS} w-full`;

const SECTION_HEADER_CLASS =
  'border-b bg-slate-50 px-4 py-2 text-sm font-medium dark:bg-slate-800';

function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

export default function PortSetupForm() {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [capital, setCapital] = useState('1000000');
  const [candleUnit, setCandleUnit] = useState(CANDLE_UNITS[0]);
  const [tickVerification, setTickVerification] = useState(false);
  const [startDate, setStartDate] = useState(defaultDate(90));
  const [startTime, setStartTime] = useState('00:00');
  const [endDate, setEndDate] = useState(defaultDate(0));
  const [endTime, setEndTime] = useState('00:00');
  const [feeRate, setFeeRate] = useState('0.100');

  function handleNext() {
    console.log('next step (mock)', {
      title,
      description,
      capital,
      candleUnit,
      tickVerification,
      startDate,
      startTime,
      endDate,
      endTime,
      feeRate,
    });
  }

  return (
    <div className="max-w-4xl space-y-6 rounded-xl border p-6 shadow-sm">
      <div className="grid grid-cols-2 gap-6">
        <div>
          <label className="mb-1.5 block text-sm font-medium">포트 제목</label>
          <input
            type="text"
            placeholder="포트폴리오 제목을 입력해 주세요."
            className={`${INPUT_CLASS} w-full`}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">
            포트 설명 <span className="font-normal text-muted-foreground">(선택사항)</span>
          </label>
          <input
            type="text"
            placeholder="포트폴리오에 대한 설명을 100자 이내로 남겨주세요."
            className={`${INPUT_CLASS} w-full`}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">기본 조건</h2>
        <div className="grid grid-cols-[1fr_1fr_2fr] divide-x rounded-md border">
          <div>
            <div className={SECTION_HEADER_CLASS}>운용자금</div>
            <div className="flex items-center gap-2 p-4">
              <input
                type="text"
                inputMode="numeric"
                className={`${INPUT_CLASS} w-full`}
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
              />
              <span className="text-sm text-muted-foreground">원</span>
            </div>
          </div>

          <div>
            <div className={SECTION_HEADER_CLASS}>봉데이터 선택</div>
            <div className="space-y-2 p-4">
              <select
                className={SELECT_CLASS}
                value={candleUnit}
                onChange={(e) => setCandleUnit(e.target.value)}
              >
                {CANDLE_UNITS.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={tickVerification}
                  onChange={(e) => setTickVerification(e.target.checked)}
                />
                틱 데이터 검증
              </label>
            </div>
          </div>

          <div>
            <div className={SECTION_HEADER_CLASS}>운용기간</div>
            <div className="space-y-2 p-4">
              <div className="flex items-center gap-2">
                <input
                  type="date"
                  className={INPUT_CLASS}
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
                <input
                  type="time"
                  className={INPUT_CLASS}
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                />
                <span className="text-sm text-muted-foreground">부터</span>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="date"
                  className={INPUT_CLASS}
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                />
                <input
                  type="time"
                  className={INPUT_CLASS}
                  value={endTime}
                  onChange={(e) => setEndTime(e.target.value)}
                />
                <span className="text-sm text-muted-foreground">까지</span>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <p className="text-xs text-muted-foreground">기간 설정 후 다음 단계에서 상세 계산 결과가 표시됩니다.</p>
                <button
                  type="button"
                  className="whitespace-nowrap rounded-md border border-input bg-background px-2 py-1 text-xs hover:bg-slate-50 dark:hover:bg-slate-800"
                  onClick={() => {
                    setStartDate(defaultDate(90));
                    setEndDate(defaultDate(0));
                  }}
                >
                  최근 최대 기간 설정
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div>
        <div className="w-full max-w-xs rounded-md border">
          <div className={SECTION_HEADER_CLASS}>수수료율</div>
          <div className="flex items-center gap-2 p-4">
            <input
              type="text"
              className={`${INPUT_CLASS} w-24`}
              value={feeRate}
              onChange={(e) => setFeeRate(e.target.value)}
            />
            <span className="text-sm text-muted-foreground">%</span>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 border-t pt-4">
        <Button type="button" variant="outline" onClick={() => console.log('cancel (mock)')}>
          취소
        </Button>
        <Button type="button" onClick={handleNext}>
          다음 단계로
        </Button>
      </div>
    </div>
  );
}
