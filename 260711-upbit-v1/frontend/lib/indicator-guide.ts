/**
 * 지표 가이드 탭 전용 정적 설명 콘텐츠.
 *
 * 숫자 예시는 여기 하드코딩하지 않는다 — 실제 계산은 lib/indicator-calc.ts + lib/guide-sample-data.ts를
 * lib/indicator-example-builder.ts가 조합해 만들고(엔진과 같은 공식으로 실시간 계산), 이 파일은
 * "무엇을/왜/어떻게 쓰는지"에 대한 텍스트만 담는다. 파라미터 키는 backend/main.py의
 * INDICATOR_CATALOG와 engine/indicators/*.py가 실제로 받는 인자명과 일치시킨다.
 */

export interface ParamNote {
  key: string;
  role: string;
}

export interface IndicatorGuideText {
  meaning: string;
  params: ParamNote[];
  formula: string;
  thresholdExample: string;
  usage: string;
}

const PRICE_SCALE_CAVEAT =
  '이 앱의 조건식은 "지표값과 숫자 threshold"만 비교합니다(지표끼리 직접 비교하는 기능은 없음). ' +
  '그래서 이 지표를 쓸 때 threshold는 보통 지금 가격대와 비슷한 값을 넣어 "가격이 이 레벨대에 있는지"를 거르는 용도로 쓰지, ' +
  '흔히 말하는 "가격이 이동평균을 상향 돌파(골든크로스)"를 그대로 표현하진 못합니다. 그런 크로스 효과가 필요하면 ' +
  'MACD나 모멘텀처럼 이미 "차이/변화율"로 계산해주는 지표를 쓰는 편이 낫습니다.';

export const INDICATOR_GUIDE: Record<string, IndicatorGuideText> = {
  SMA: {
    meaning:
      '최근 period개 종가를 그대로 산술평균한 값입니다. engine/indicators/trend.py의 create_sma가 backtrader의 SMA(period)를 그대로 호출합니다.',
    params: [{ key: 'period', role: '평균을 낼 봉 개수. 크게 잡을수록 느리고 완만하게, 작게 잡을수록 가격을 빠르게 따라갑니다.' }],
    formula: 'SMA = (최근 period개 종가의 합) ÷ period',
    thresholdExample: `${PRICE_SCALE_CAVEAT}`,
    usage: '단독보다는 "SMA(50) 위/아래에서만 매수" 같은 큰 흐름 필터로, 다른 오실레이터 조건과 AND로 묶어 자주 씁니다.',
  },
  EMA: {
    meaning:
      '최근 가격에 더 큰 가중치를 주는 이동평균입니다. 처음 period개는 SMA로 시작값을 잡고, 그다음 봉부터 지수적으로 스무딩합니다.',
    params: [{ key: 'period', role: '가중치 감쇠 속도를 정하는 기간. α=2/(period+1)가 매 봉 새 종가에 주는 가중치입니다.' }],
    formula: 'EMA_t = 종가_t × α + EMA_{t-1} × (1-α),  α = 2 ÷ (period+1)  (시작값은 처음 period개 종가의 SMA)',
    thresholdExample: `${PRICE_SCALE_CAVEAT}`,
    usage: 'SMA보다 최근 변화에 민감하게 반응해, 추세 전환을 좀 더 빨리 잡고 싶을 때 SMA 대신 씁니다.',
  },
  WMA: {
    meaning: '최근 봉일수록 선형으로 더 큰 가중치를 주는 이동평균입니다(가장 최근 봉의 가중치가 period로 가장 큼).',
    params: [{ key: 'period', role: '평균에 포함할 봉 개수이자 최대 가중치. 가중치는 1,2,...,period 순으로 매겨집니다.' }],
    formula: 'WMA = (종가_{t-period+1}×1 + ... + 종가_t×period) ÷ (1+2+...+period)',
    thresholdExample: `${PRICE_SCALE_CAVEAT}`,
    usage: 'SMA·EMA와 마찬가지로 절대 가격 레벨 필터로 쓰되, 최근 봉에 가장 민감하게 반응하고 싶을 때 선택합니다.',
  },
  RSI: {
    meaning:
      '최근 period봉 동안 "평균 상승폭"이 "평균 하락폭"보다 얼마나 큰지를 0~100 사이 값으로 표준화합니다. backtrader 기본 RSI로, 평균은 단순평균이 아니라 Wilder 방식(지수 스무딩과 비슷하게 이전 평균을 참고해 누적)으로 계산됩니다.',
    params: [{ key: 'period', role: '평균 상승폭/하락폭을 계산할 봉 개수. 작을수록 값이 더 빠르고 크게 출렁입니다.' }],
    formula:
      '평균상승폭, 평균하락폭 = 최근 period개 변화분의 평균(첫 계산만 단순평균, 이후는 이전 평균을 반영해 누적)\nRS = 평균상승폭 ÷ 평균하락폭\nRSI = 100 − 100 ÷ (1 + RS)',
    thresholdExample:
      'RSI < 30 → 최근 하락폭이 상승폭을 크게 압도해 "과매도" 구간에 들어왔다는 뜻으로 흔히 해석(반등 기대 매수). RSI > 70 → 반대로 "과매수"(조정 경계, 매도 검토).',
    usage: '단독 매수 신호보다는, MARKET_TREND 같은 큰 추세 필터와 함께 "추세는 살아있는데 일시적으로 눌린 구간"을 잡는 데 자주 씁니다.',
  },
  MACD_line: {
    meaning:
      '단기 EMA에서 장기 EMA를 뺀 값으로, 두 이동평균의 괴리(모멘텀의 방향과 세기)를 나타냅니다. 조건식에서는 backtrader MACD 지표의 macd 서브라인(engine/condition_tree.py의 get_indicator_value가 obj.macd를 읽음)을 씁니다.',
    params: [
      { key: 'fast', role: '단기 EMA 기간. 가격 변화에 더 민감한 쪽.' },
      { key: 'slow', role: '장기 EMA 기간. fast보다 느리게 움직이는 기준선.' },
      { key: 'signal', role: 'MACD Line 자체를 다시 평활화하는 EMA 기간 — MACD_signal 지표가 이 값을 씁니다.' },
    ],
    formula: 'MACD Line = EMA(fast) − EMA(slow)',
    thresholdExample:
      'MACD Line > 0 → 단기 EMA가 장기 EMA보다 위, 즉 상승 모멘텀이 우세하다는 뜻. threshold 0을 기준으로 부호가 바뀌는 지점을 "모멘텀 전환"으로 봅니다.',
    usage: 'MACD_line과 MACD_signal을 매수/매도 조건에 각각 넣어, "Line이 Signal보다 크면 매수 유지, 작아지면 매도"처럼 두 지표를 짝지어 씁니다.',
  },
  MACD_signal: {
    meaning:
      'MACD Line을 다시 signal기간 EMA로 평활화한 값입니다. MACD Line과의 교차로 매매 타이밍을 잡을 때 기준선 역할을 합니다. get_indicator_value는 obj.signal 서브라인을 읽습니다.',
    params: [
      { key: 'fast', role: 'MACD Line 계산에 쓰는 단기 EMA 기간(이 지표 자체의 파라미터이기도 함 — 같은 MACD 객체를 공유).' },
      { key: 'slow', role: 'MACD Line 계산에 쓰는 장기 EMA 기간.' },
      { key: 'signal', role: 'MACD Line을 평활화하는 기간. 클수록 완만하고 느리게 따라갑니다.' },
    ],
    formula: 'MACD Signal = MACD Line의 signal기간 EMA',
    thresholdExample:
      'MACD Line이 Signal을 상향 돌파(Line > Signal)하는 시점을 흔히 매수 신호로, 하향 돌파를 매도 신호로 봅니다. threshold는 보통 0 근처(zero-cross)를 씁니다.',
    usage: 'MACD_line ">" 조건과 MACD_signal 값을 서로 다른 블록에 넣기보다, 보통 "MACD_line > 0"과 "MACD_signal > 0"을 함께 걸어 상승 모멘텀 구간만 남기는 식으로 씁니다.',
  },
  STOCH_K: {
    meaning:
      '최근 k_period봉의 최고가~최저가 범위 안에서 현재 종가가 어디에 위치하는지를 0~100으로 나타냅니다. get_indicator_value는 obj.percK를 읽습니다.',
    params: [
      { key: 'k_period', role: '%K를 계산할 때 "최근 몇 봉의 고가/저가"를 볼지 정하는 기간. backtrader Stochastic의 period 인자.' },
      { key: 'd_period', role: '%K를 다시 평균 내 %D를 만들 때 쓰는 평균 기간(이 지표 값 자체에는 영향 없음, STOCH_D 파라미터).' },
    ],
    formula: '%K = (종가 − k_period봉 최저가) ÷ (k_period봉 최고가 − k_period봉 최저가) × 100',
    thresholdExample:
      '%K < 20 → 최근 k_period봉의 저점 근처에서 거래되고 있다(과매도). %K > 80 → 고점 근처(과매수). RSI보다 더 예민하게(더 자주) 신호가 납니다.',
    usage: '변동성이 큰 알트코인의 단기 되돌림을 잡을 때 RSI 대신, 혹은 RSI와 같이 AND로 묶어 이중 확인용으로 씁니다.',
  },
  STOCH_D: {
    meaning:
      '%K를 다시 d_period봉 단순이동평균한 값으로, %K보다 완만하게 움직여 노이즈(잔파동)를 줄인 신호선입니다. get_indicator_value는 obj.percD를 읽습니다.',
    params: [
      { key: 'k_period', role: '먼저 %K를 계산할 때 쓰는 "최근 몇 봉의 고가/저가"를 볼지 정하는 기간(원재료).' },
      { key: 'd_period', role: '그렇게 나온 %K를 다시 몇 봉 평균 낼지. 값 자체(%D)를 결정하는 파라미터입니다.' },
    ],
    formula: '%D = %K값들의 d_period봉 단순이동평균 = SMA(%K, d_period)',
    thresholdExample:
      '%D < 20이면서 %K가 %D를 상향 돌파하는 순간을 "바닥 확인 후 반등 시작"으로 흔히 해석합니다. 이 앱은 %K/%D 각각을 독립된 threshold 조건으로만 걸 수 있어, 교차 자체보다는 "%D가 과매도 구간에 있는지" 필터로 주로 씁니다.',
    usage: '%K는 빠른 신호, %D는 그 신호가 진짜인지 확인하는 완만한 신호로 역할을 나눠, 두 조건을 함께 걸어 다이는 신호를 줄이는 데 씁니다.',
  },
  CCI: {
    meaning:
      '가격(정확히는 고가·저가·종가 평균인 "전형가")이 자신의 평균에서 얼마나(평균편차 대비 몇 배) 벗어났는지를 나타내는 지표입니다. 0.015는 값의 약 70~80%가 -100~100 사이에 오도록 맞춘 상수입니다.',
    params: [{ key: 'period', role: '전형가의 평균과 평균편차를 계산할 봉 개수.' }],
    formula:
      '전형가 TP = (고가+저가+종가) ÷ 3\nCCI = (TP − TP의 period봉 평균) ÷ (0.015 × TP의 평균절대편차)',
    thresholdExample: 'CCI < -100 → 과매도, CCI > 100 → 과매수로 흔히 해석합니다. RSI/스토캐스틱보다 상한·하한이 없어 극단값이 더 크게 튈 수 있습니다.',
    usage: '박스권을 벗어나는 강한 추세 시작을 포착하거나, ±100 재진입을 "추세 소멸"로 보는 역추세 신호로 씁니다.',
  },
  WILLIAMS_R: {
    meaning: '스토캐스틱 %K와 계산 원리는 같지만 부호가 반대이고 0~-100 범위로 표현됩니다(0에 가까울수록 고점 근처).',
    params: [{ key: 'period', role: '최고가/최저가를 조회할 봉 개수. 스토캐스틱의 k_period와 같은 역할.' }],
    formula: '%R = (period봉 최고가 − 종가) ÷ (period봉 최고가 − period봉 최저가) × -100',
    thresholdExample: '%R < -80 → 최근 저점 근처(과매도). %R > -20 → 최근 고점 근처(과매수). 0에 가까울수록 "이 구간의 신고가권", -100에 가까울수록 "이 구간의 신저가권"입니다.',
    usage: '스토캐스틱과 해석이 거의 같아 취향껏 하나만 선택해 쓰거나, 다른 오실레이터와 조합해 이중 확인용으로 씁니다.',
  },
  BB_upper: {
    meaning:
      '20봉 이동평균(중간선)에 표준편차의 2배(devfactor=2.0, 고정)를 더한 상단 밴드입니다. get_indicator_value는 obj.top을 읽습니다.',
    params: [{ key: 'period', role: '중간선(이동평균)과 표준편차를 계산할 봉 개수.' }],
    formula: '상단 = SMA(period) + 2 × 표준편차(period)',
    thresholdExample: `${PRICE_SCALE_CAVEAT}`,
    usage: '종가가 상단을 넘나드는지 자체보다는, "지금 가격이 상단 밴드 값보다 높은 절대 레벨"인지 필터로 쓰거나 ATR과 함께 변동성 국면을 가늠하는 보조 지표로 씁니다.',
  },
  BB_lower: {
    meaning:
      '20봉 이동평균(중간선)에서 표준편차의 2배를 뺀 하단 밴드입니다. get_indicator_value는 obj.bot을 읽습니다.',
    params: [{ key: 'period', role: '중간선(이동평균)과 표준편차를 계산할 봉 개수.' }],
    formula: '하단 = SMA(period) − 2 × 표준편차(period)',
    thresholdExample: `${PRICE_SCALE_CAVEAT}`,
    usage: '변동성이 수축했다가 하단을 이탈 후 재진입하는 구간을 과매도 반등 후보로 보는 전략에 흔히 씁니다.',
  },
  BB_middle: {
    meaning: '볼린저밴드의 기준이 되는 단순 이동평균선입니다. 값 자체는 SMA(period)와 완전히 같습니다. get_indicator_value는 obj.mid를 읽습니다.',
    params: [{ key: 'period', role: '이동평균 기간. 상단/하단 밴드와 공유합니다.' }],
    formula: '중간선 = SMA(period)  (상단/하단과 동일한 이동평균)',
    thresholdExample: `${PRICE_SCALE_CAVEAT}`,
    usage: 'SMA를 그대로 쓰는 것과 동일한 효과라, 볼린저 상/하단과 같은 화면에서 기준선으로 같이 쓸 때 의미가 있습니다.',
  },
  ATR: {
    meaning:
      '한 봉에서 실제로 얼마나 크게 움직였는지(True Range: 고가-저가, |고가-전봉종가|, |저가-전봉종가| 중 최댓값)를 period봉 동안 평활 평균한 변동성 크기입니다.',
    params: [{ key: 'period', role: 'True Range를 평활할 봉 개수. RSI처럼 Wilder 방식으로 누적됩니다.' }],
    formula: 'True Range = max(고가−저가, |고가−전봉종가|, |저가−전봉종가|)\nATR = True Range의 period봉 Wilder 평활 평균',
    thresholdExample:
      '값 자체보다 "종가 + ATR×배수"를 다른 조건의 threshold로 활용하는 경우가 많습니다. 예: 전봉 종가+ATR×2를 오늘 고가가 넘으면 변동성 돌파로 봅니다(ATR 카드는 그 기준가를 표로 같이 보여줍니다).',
    usage: '손절폭이나 돌파 매매 기준가를 "고정 %"가 아니라 "그 코인 특유의 변동성"에 맞춰 정할 때 씁니다.',
  },
  OBV: {
    meaning:
      '종가가 전봉보다 오른 봉은 그날 거래량을 더하고, 내린 봉은 뺀 누적값입니다. engine/indicators/volume.py의 OBV.next()가 매 봉 이 규칙으로 누적합니다.',
    params: [],
    formula: '종가 상승 시: OBV_t = OBV_{t-1} + 거래량_t\n종가 하락 시: OBV_t = OBV_{t-1} − 거래량_t\n종가 보합 시: OBV_t = OBV_{t-1}',
    thresholdExample:
      '코인마다 누적 스케일이 완전히 달라 절대 threshold보다는 "OBV가 최근 상승세인지"를 다른 방식으로 확인하는 데 씁니다. 값이 계속 우상향하면 상승 캔들에 거래량이 더 크게 실리고 있다는 뜻입니다.',
    usage: '가격은 오르는데 OBV가 못 따라 오르면(다이버전스) 상승이 힘을 잃고 있다는 경고로 흔히 씁니다.',
  },
  VOLUME_SMA: {
    meaning: '최근 period봉의 거래량(수량 기준)을 산술평균한 값입니다. 현재 거래량이 평소보다 급등했는지 비교하는 기준선으로 씁니다.',
    params: [{ key: 'period', role: '거래량을 평균낼 봉 개수.' }],
    formula: 'VOLUME_SMA = 최근 period개 거래량의 평균',
    thresholdExample: '코인마다 유통량이 달라 절대값 비교보다는 "지금 거래량이 이 값의 N배"인지로 해석합니다(이 앱은 지표끼리 직접 나눌 수는 없어, 대략 이 값의 배수를 threshold에 직접 넣어 씁니다).',
    usage: '거래량 급증과 가격 조건을 AND로 묶어 "관심이 몰리는 순간의 돌파"만 남기는 필터로 씁니다.',
  },
  TRADE_VALUE: {
    meaning:
      '해당 봉에서 실제로 오간 금액(원)입니다. 업비트 캔들 API의 candle_acc_trade_price를 upbit_data_service.py가 trade_value 컬럼으로 저장하고, engine/indicators/volume.py의 create_trade_value가 그 값을 그대로 돌려줍니다. 거래량(수량)과 달리 "가격×수량"이 반영돼 있어 저가 잡코인의 수량 착시가 없습니다.',
    params: [],
    formula: '거래대금 = 그 봉에서 체결된 모든 거래의 (체결가 × 체결수량) 합',
    thresholdExample: '예: 임계값 5,000,000,000(50억)에 연산자 ">="를 걸면, 해당 봉의 거래대금이 50억 원 이상인 순간만 조건이 참이 됩니다 — 절대 금액 기준의 "큰손 유입" 필터입니다.',
    usage: '"거래량은 많지만 저가라 거래대금은 작은" 잡코인을 걸러내고, 실제로 큰 자금이 들어온 종목·순간만 남기는 데 씁니다.',
  },
  TRADE_VALUE_SMA: {
    meaning: '최근 period봉의 거래대금(원)을 산술평균한 값입니다. 현재 거래대금이 평소보다 급증했는지 비교하는 기준선입니다.',
    params: [{ key: 'period', role: '거래대금을 평균낼 봉 개수.' }],
    formula: 'TRADE_VALUE_SMA = 최근 period개 거래대금의 평균',
    thresholdExample: 'period=20 기준으로 최근 거래대금이 이 평균의 2배를 넘으면 "평소보다 자금이 몰린 급증 구간"으로 해석하는 식으로 씁니다.',
    usage: 'TRADE_VALUE(원시값)로 절대 규모 필터를 걸고, TRADE_VALUE_SMA로는 "그 코인 기준 평소 대비 얼마나 튀었는지"를 함께 보는 조합이 유용합니다.',
  },
  STOP_LOSS_PCT: {
    meaning:
      '캔들 지표가 아니라 보유 중인 포지션의 "진입가 대비 현재 수익률(%)"입니다. engine/condition_strategy.py가 포지션이 열려 있는 매 봉마다 (현재 종가−진입가)/진입가×100으로 계산해 넘깁니다.',
    params: [],
    formula: '수익률(%) = (현재 종가 − 진입가) ÷ 진입가 × 100',
    thresholdExample:
      '매도 조건 전용(sellOnly)이고 연산자가 "≤"로 고정돼 있습니다(UI에서 연산자 선택 자체를 숨김). threshold는 보통 음수(예: -5)를 넣고, 수익률이 그 값 이하로 떨어지는 순간 매도합니다. 예: 진입가 100,000원, threshold -5면 현재가 95,000원 이하에서 매도.',
    usage: '매수 조건과 무관하게 항상 걸어두는 "최소한의 손실 제한" 안전장치로, 다른 매도 조건들과 OR로 묶어 씁니다.',
  },
  TAKE_PROFIT_PCT: {
    meaning: 'STOP_LOSS_PCT와 같은 방식으로 계산되는 진입가 대비 수익률(%)이지만, 수익 실현 쪽 조건입니다.',
    params: [],
    formula: '수익률(%) = (현재 종가 − 진입가) ÷ 진입가 × 100',
    thresholdExample:
      '매도 조건 전용, 연산자 "≥" 고정. threshold는 보통 양수(예: 10)를 넣고, 수익률이 그 값 이상이 되는 순간 매도합니다. 예: 진입가 100,000원, threshold 10이면 현재가 110,000원 이상에서 매도.',
    usage: 'STOP_LOSS_PCT와 짝을 이뤄 "익절/손절 라인을 동시에 걸어두는" 가장 기본적인 리스크 관리 조합입니다.',
  },
  HOLDING_PERIOD_BARS: {
    meaning:
      '캔들 지표가 아니라 포지션을 진입한 뒤 지금까지 지난 봉의 개수입니다. engine/condition_strategy.py가 진입 시점의 봉 번호를 기억해두고, 매 봉마다 (현재 봉 번호 − 진입 봉 번호)로 계산합니다.',
    params: [],
    formula: '보유 봉수 = 현재 봉 번호 − 진입한 봉 번호',
    thresholdExample:
      '매도 조건 전용, 연산자 "≥" 고정. threshold는 봉 개수이지 날짜가 아닙니다 — 15분봉에서 threshold 20이면 5시간, 일봉에서 threshold 20이면 20일 후 매도됩니다(선택한 봉데이터 타입에 따라 실제 경과 시간이 달라짐).',
    usage: '방향성 없이 오래 물려 있는 포지션을 정리하는 "시간 손절"로, STOP_LOSS_PCT/TAKE_PROFIT_PCT와 함께 OR로 묶어 씁니다.',
  },
  FIB_382: {
    meaning: '최근 period봉의 스윙 고점(최고가)과 저점(최저가) 사이에서, 고점 대비 38.2% 되돌아온 가격입니다.',
    params: [{ key: 'period', role: '스윙 고점/저점을 찾을 봉 개수.' }],
    formula: '최고가 = period봉 최고가, 최저가 = period봉 최저가\nFIB_382 = 최고가 − (최고가 − 최저가) × 0.382',
    thresholdExample: `${'이 앱의 조건식은 "지표값과 숫자 threshold"만 비교합니다. 이 지표를 쓸 때 threshold는 보통 지금 가격대와 비슷한 값을 넣어 "가격이 이 지지/저항 레벨 근처에 있는지"를 거르는 용도로 씁니다.'}`,
    usage: '상승 추세 중 조정이 38.2%선에서 멈추는지 확인해, 그 근처에서 반등을 노리는 눌림목 매수 조건으로 씁니다.',
  },
  FIB_500: {
    meaning: '최근 period봉의 스윙 고점과 저점의 정중앙(50%) 되돌림 가격입니다.',
    params: [{ key: 'period', role: '스윙 고점/저점을 찾을 봉 개수.' }],
    formula: 'FIB_500 = 최고가 − (최고가 − 최저가) × 0.5',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '38.2%/61.8%와 함께 3단계 되돌림 구간을 나눠, 가격이 어느 구간에 있는지로 조정의 깊이를 가늠할 때 씁니다.',
  },
  FIB_618: {
    meaning: '황금비율로 불리는 61.8% 되돌림 가격입니다. 조정이 깊게 들어와도 추세가 살아있는지 가늠하는 마지노선급 지지/저항으로 흔히 해석합니다.',
    params: [{ key: 'period', role: '스윙 고점/저점을 찾을 봉 개수.' }],
    formula: 'FIB_618 = 최고가 − (최고가 − 최저가) × 0.618',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '61.8%선까지 눌리고도 지지되면 추세가 아직 살아있다고 보고, 반대로 깨지면 추세 전환으로 보는 필터로 씁니다.',
  },
  PIVOT_P: {
    meaning: '직전 1봉의 고가·저가·종가 평균입니다. 오늘 가격이 이 선 위/아래 어디서 노는지로 매수/매도 심리 우위를 가늠하는 전통적 지표입니다.',
    params: [],
    formula: 'Pivot = (직전 봉 고가 + 직전 봉 저가 + 직전 봉 종가) ÷ 3',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '종가가 Pivot 위/아래 어느 쪽에 있는지를 다른 오실레이터 조건과 AND로 묶어, 그날의 우세한 방향으로만 진입하는 필터로 씁니다.',
  },
  PIVOT_R1: {
    meaning: 'Pivot 기준선 대비 1차 저항선입니다. 종가가 이 선을 넘으면 상승 모멘텀이 강하다고 흔히 해석합니다.',
    params: [],
    formula: 'R1 = Pivot × 2 − 직전 봉 저가',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: '종가가 R1을 상향 돌파하는 걸 돌파 매수 신호로, 혹은 R1 근처를 저항으로 보고 매도 신호로 반대로 쓰기도 합니다.',
  },
  PIVOT_S1: {
    meaning: 'Pivot 기준선 대비 1차 지지선입니다. 종가가 이 선 아래로 내려가면 하락 압력이 강하다고 흔히 해석합니다.',
    params: [],
    formula: 'S1 = Pivot × 2 − 직전 봉 고가',
    thresholdExample: '이 앱의 조건식은 지표값과 숫자 threshold만 비교합니다. threshold는 보통 현재 가격대 근처 값을 넣어 레벨 필터로 씁니다.',
    usage: 'S1 근처에서 반등을 노리는 매수 조건, 혹은 S1 하향 이탈을 손절/추가 하락 신호로 씁니다.',
  },
  MARKET_TREND: {
    meaning:
      '대상 코인이 아니라 KRW-BTC의 "종가 − 자기 자신의 이동평균" 값입니다. engine/indicators/market.py가 백엔드에서 병합해준 KRW-BTC 종가(self.data.extra)로 계산합니다. 알트코인이 BTC 흐름을 따라가는 경향을 이용한 시장 전체 필터입니다.',
    params: [{ key: 'period', role: 'KRW-BTC 종가의 이동평균을 계산할 봉 개수.' }],
    formula: '시장 추세 = KRW-BTC 종가 − KRW-BTC 종가의 period봉 이동평균',
    thresholdExample: '연산자 "<", threshold 0 → BTC 종가가 자기 이동평균보다 낮을 때(BTC 하락 추세일 때) 조건이 참. 반대로 ">" 0이면 BTC가 상승 추세일 때만 참이 됩니다.',
    usage: '알트코인 매수 조건에 "BTC가 하락 추세가 아닐 때만"이라는 시장 필터를 AND로 추가해, 전체 시장이 흔들릴 때 매수를 쉬는 용도로 씁니다.',
  },
  MOMENTUM_PCT: {
    meaning: 'period봉 전 종가 대비 현재 종가가 몇 % 올랐거나 내렸는지입니다. backtrader의 ROC100(Rate of Change ×100)을 그대로 씁니다.',
    params: [{ key: 'period', role: '몇 봉 전 가격과 비교할지 정하는 기간.' }],
    formula: '모멘텀(%) = (현재 종가 − period봉 전 종가) ÷ period봉 전 종가 × 100',
    thresholdExample:
      '연산자 ">", threshold 3 → period봉 전보다 3% 이상 오른 상태(상승 모멘텀 진입)를 포착. 연산자 "<", threshold -5 → period봉 전보다 5% 이상 급락한 상태(눌림목/역추세 진입)를 포착.',
    usage: '단기 추세 추종(threshold 양수)이나 급락 후 반등 노림(threshold 음수) 두 가지 반대 방향 전략 모두에 씁니다.',
  },
  BTC_CORRELATION: {
    meaning: '대상 코인과 KRW-BTC의 봉 대비 등락률(%)을 최근 period봉 모아 계산한 Pearson 상관계수입니다. engine/indicators/market.py의 RollingCorrelation이 이 값을 계산합니다.',
    params: [{ key: 'period', role: '상관계수를 계산할 롤링 윈도우 봉 개수.' }],
    formula: '등락률_t = (종가_t − 종가_{t-1}) ÷ 종가_{t-1} × 100\n상관계수 = Pearson(대상코인 등락률_[t-period+1..t], KRW-BTC 등락률_[t-period+1..t])',
    thresholdExample: '값은 -1~1 범위입니다. 1에 가까울수록 BTC와 같은 방향으로, -1에 가까울수록 반대 방향으로 움직입니다. 예: 임계값 0.3, 연산자 "<"면 BTC와의 동조화가 약해진(디커플링) 구간을 포착합니다.',
    usage: '알트코인 매수 조건에 "BTC와 상관관계가 낮을 때만"이라는 필터를 추가해, 시장 전체 방향이 아니라 그 코인 고유의 움직임을 노리는 전략에 씁니다.',
  },
  USDT_CORRELATION: {
    meaning: '대상 코인과 KRW-USDT(테더)의 봉 대비 등락률(%)을 최근 period봉 모아 계산한 Pearson 상관계수입니다.',
    params: [{ key: 'period', role: '상관계수를 계산할 롤링 윈도우 봉 개수.' }],
    formula: '등락률_t = (종가_t − 종가_{t-1}) ÷ 종가_{t-1} × 100\n상관계수 = Pearson(대상코인 등락률_[t-period+1..t], KRW-USDT 등락률_[t-period+1..t])',
    thresholdExample: '값은 -1~1 범위입니다. 예: 임계값 0.5, 연산자 ">"면 원화 유동성(테더) 흐름과 강하게 같이 움직이는 구간만 남깁니다.',
    usage: 'BTC 상관계수와 함께 걸어, "BTC와는 무관하지만 전체 원화 유동성 흐름과는 같이 가는" 것처럼 세밀한 시장 필터 조합을 만들 때 씁니다.',
  },
};
