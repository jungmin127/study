# Volume Profile (VPVR) 지표 설계

## 목적

`docs/superpowers/specs_v1/2026-07-27-strategy-source-classification.md`의 "선행지표류" B 레이어 중
마지막 항목인 Volume Profile(VPVR, Visible-Range Volume Profile)을 조건 빌더에 새 지표로 추가한다.
[[upbit-v1-external-indicator-roadmap]]에서 정한 순서(체결강도 → VPIN → Volume Profile)의 세 번째이자
현재 로드맵상 마지막 B 레이어 항목이다. VPIN과 마찬가지로 캔들(OHLCV) 데이터만으로 계산 가능하며,
틱 데이터나 외부 API가 필요 없다.

## 배경

시간이 아니라 **가격대별 거래량**을 집계하는 지표다. 거래량이 몰린 가격대(Point of Control, POC)는
시장이 "공정하다"고 합의한 가격으로 해석되어 반등/저항이 자주 일어나고, 거래량이 적었던 구간(Low
Volume Node)은 가격이 빠르게 통과하는 경향이 있다는 게 활용 근거다. 실측 웹서치로 확인한 표준
방법론(TradingView 등 상용 구현 포함)은 캔들 데이터만으로 근사할 때 아래 네 가지 방식 중 하나를
쓴다: **Close**(종가 1점에 전량 배정), **Middle**((고가+저가)/2), **Weighted**(종가의 고가-저가 내
위치로 가중 추정), 또는 **봉의 고가-저가 범위 전체에 거래량을 겹침 비율만큼 분배**하는 방식. 이 중
마지막 방식이 실제 상용 Volume Profile 구현과 가장 가깝고, VPIN 때 정확한 방법론(BVC)을 택한
전례를 그대로 따라 이 프로젝트도 이 방식을 채택한다(아래 "검토한 접근" 참고).

## 아키텍처

### 검토한 접근

**거래량 분배 방식**
- **A. 봉의 고가-저가 범위에 겹침 비율만큼 균등 분배 (채택)** — 실제 Volume Profile 표준 방식에
  가장 가깝다. 변동성이 큰 봉(예: 15분봉에서 급등락)에서도 거래량이 그 범위 전체에 자연스럽게
  퍼진다. 계산량은 봉당 `period × NUM_BINS` 수준으로, `RollingCorrelation`(봉당 `period`)보다는
  무겁지만 이 프로젝트 규모의 백테스트에서 성능 문제가 될 정도는 아니다.
- **B. 대표가 1점(예: (고가+저가+종가)/3)에 전량 배정 (기각)** — 구현은 더 간단하지만, 고가-저가
  폭이 큰 봉에서 거래량 위치가 실제보다 왜곡되어 "가격대별 거래량 분포"라는 지표의 취지에서 멀어진다.

**계산 위치**
- **A. 백엔드 병합 단계에서 pandas로 사전 계산 (기각)** — VPIN 스펙에서와 동일한 이유로 기각한다:
  `period` 파라미터를 가진 지표는 조건 트리 안에서 서로 다른 `period` 값의 블록이 동시에 쓰일 수
  있어(예: 매수 조건엔 `period=50`, 매도 조건엔 `period=100`), 컬럼 하나짜리 병합으로는 표현 불가능.
- **B. 커스텀 `bt.Indicator`, backtrader 라이브 계산 (채택)** — `PivotPoints`/`VolumeBarVPIN`과 같은
  패턴. 대상 코인 자신의 `data.high`/`low`/`volume`만 쓰고 다른 마켓 캔들도, 외부 API도, 백엔드
  병합 로직도 필요 없다.

### 계산 알고리즘

1. **롤링 윈도우 수집**: 최근 `period`(기본값 50)개 봉의 `(high, low, volume)`을 `deque(maxlen=period)`
   세 개로 유지한다.
2. **가격 구간(bin) 설정**: 윈도우 내 최고가(`window_high` = 수집된 high들의 최댓값)와 최저가
   (`window_low` = 수집된 low들의 최솟값) 사이를 **고정 24개 bin**(`NUM_BINS`, 모듈 상수, 파라미터로
   노출하지 않음)으로 균등 분할한다. `bin_width = (window_high - window_low) / NUM_BINS`.
3. **봉별 거래량 분배**: 윈도우 안의 각 봉 `(h, l, v)`에 대해
   - `h == l`(도지/무변동 봉)이면 그 가격이 속한 bin 하나에 `v` 전체를 배정(0으로 나누기 방지, 가격이
     `window_high`와 정확히 일치하는 경계 케이스는 마지막 bin으로 클램프).
   - 그 외에는 각 bin에 대해 `overlap = min(h, bin_top) - max(l, bin_bottom)`을 구해 `overlap > 0`이면
     `bin_volume[i] += v * (overlap / (h - l))`로 겹치는 비율만큼 분배한다.
4. **POC(Point of Control)**: 누적 거래량이 가장 큰 bin의 중간값(가격).
5. **Value Area(VAH/VAL)**: POC bin에서 시작해, 위/아래 인접 bin 중 거래량이 더 큰 쪽을 번갈아
   편입시키며 누적 거래량이 전체 거래량의 **70%**(`VALUE_AREA_PCT`, 모듈 상수, 업계 표준)에 도달할
   때까지 확장한다. VAH = 편입된 bin 중 최상단 bin의 윗값, VAL = 최하단 bin의 아랫값.
6. **완전히 평평한 윈도우**(`window_high == window_low`, 극단적으로 무변동): bin 분할 자체가
   무의미하므로 POC=VAH=VAL=그 가격 하나로 처리 — `VolumeBarVPIN`이 가격변화 표준편차 0일 때
   `z=0`으로 처리하는 것과 같은 결의 방어 코드.
7. **매 봉마다 처음부터 재계산**한다(캐시/증분 갱신 없음) — `RollingCorrelation`도 매 봉 전체
   윈도우를 다시 계산하는 것과 동일한 단순함 우선 접근.

### 파라미터

`period` 하나(기본값 50)로 롤링 윈도우 크기를 제어한다. 이 프로젝트의 다른 지표들과 달리 기본값을
50으로 잡은 이유: 가격대별 거래량 분포가 의미 있게 쌓이려면 보통 유동성/피봇 지표(`period=14~20`)
보다 훨씬 긴 창이 필요하다(업계 관습적으로 세션/데일리 단위를 쓰는데, 이 프로젝트는 단일 timeframe
캔들 구조라 그 대신 더 긴 봉 개수로 근사한다). `NUM_BINS`(24)와 `VALUE_AREA_PCT`(0.7)는 내부 상수로
고정하고 파라미터로 노출하지 않는다 — 다른 지표들의 "파라미터는 `period` 하나" 관례를 그대로 따른다.

## 상세 설계

### 1. 지표 구현 — `engine/indicators/price_levels.py` (기존 파일에 추가)

가격대(지지/저항) 계열 지표(FIB, Pivot Points)가 이미 모여있는 파일이라 여기에 추가한다(새 파일
불필요).

```python
from collections import deque

NUM_BINS = 24
VALUE_AREA_PCT = 0.7


class VolumeProfile(bt.Indicator):
    """최근 period개 봉의 고가-저가 범위에 거래량을 겹침 비율만큼 분배해 가격대별 거래량
    분포(Volume Profile)를 만들고, 그 분포에서 POC(거래량 최다 가격대)와 Value Area
    상단/하단(VAH/VAL)을 뽑아낸다. 틱 데이터 없이 캔들만으로 계산하는 근사치다."""

    lines = ("poc", "vah", "val")
    params = (("period", 50),)

    def __init__(self) -> None:
        period = self.p.period
        self._highs: deque = deque(maxlen=period)
        self._lows: deque = deque(maxlen=period)
        self._volumes: deque = deque(maxlen=period)

    def _compute(self) -> None:
        self._highs.append(self.data.high[0])
        self._lows.append(self.data.low[0])
        self._volumes.append(self.data.volume[0])

        if len(self._highs) < self.p.period:
            self.lines.poc[0] = float("nan")
            self.lines.vah[0] = float("nan")
            self.lines.val[0] = float("nan")
            return

        window_high = max(self._highs)
        window_low = min(self._lows)

        if window_high == window_low:
            self.lines.poc[0] = window_high
            self.lines.vah[0] = window_high
            self.lines.val[0] = window_high
            return

        bin_width = (window_high - window_low) / NUM_BINS
        bin_volumes = [0.0] * NUM_BINS

        for h, l, v in zip(self._highs, self._lows, self._volumes):
            if h == l:
                idx = min(int((h - window_low) / bin_width), NUM_BINS - 1)
                bin_volumes[idx] += v
                continue
            for i in range(NUM_BINS):
                bin_bottom = window_low + i * bin_width
                bin_top = bin_bottom + bin_width
                overlap = min(h, bin_top) - max(l, bin_bottom)
                if overlap > 0:
                    bin_volumes[i] += v * (overlap / (h - l))

        total_volume = sum(bin_volumes)
        poc_idx = max(range(NUM_BINS), key=lambda i: bin_volumes[i])
        poc_price = window_low + (poc_idx + 0.5) * bin_width

        lo = hi = poc_idx
        accumulated = bin_volumes[poc_idx]
        target = total_volume * VALUE_AREA_PCT
        while accumulated < target and (lo > 0 or hi < NUM_BINS - 1):
            expand_lo = bin_volumes[lo - 1] if lo > 0 else -1.0
            expand_hi = bin_volumes[hi + 1] if hi < NUM_BINS - 1 else -1.0
            if expand_hi >= expand_lo:
                hi += 1
                accumulated += expand_hi
            else:
                lo -= 1
                accumulated += expand_lo

        self.lines.poc[0] = poc_price
        self.lines.vah[0] = window_low + (hi + 1) * bin_width
        self.lines.val[0] = window_low + lo * bin_width

    def next(self) -> None:
        self._compute()


def create_vpvr_poc(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 50))
    return VolumeProfile(data, period=period)


def create_vpvr_vah(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 50))
    return VolumeProfile(data, period=period)


def create_vpvr_val(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 50))
    return VolumeProfile(data, period=period)
```

**VPIN/OBV와 달리 `nextstart()`/`next()` 분리가 필요 없는 이유**: `VolumeProfile`은 매 봉 윈도우
전체를 처음부터 재계산하고 자기 자신의 이전 값(`self.lines.poc[-1]` 등)을 참조하지 않는다(forward-fill이
없다) — `PivotPoints`가 `next()` 하나로 충분한 것과 같은 이유다.

**워밍업이 다른 지표보다 정확한 이유**: VPIN은 "버킷이 채워지는 것"과 "버킷이 `period`개 모이는 것"이
분리된 2단계 워밍업이라 `max_required_period()`의 근사치(`period`)가 실제 필요 봉 수보다 훨씬
작았다(VPIN 최종 리뷰에서 발견된 기존 한계, [[upbit-v1-external-indicator-roadmap]] 참고 — 알려진
채로 남겨둔 사항). VPVR은 `period`개 봉이 모이면 그 자리에서 바로 완전한 분포를 계산할 수 있어
1단계 워밍업뿐이다 — 즉 `max_required_period()`가 반환하는 `period`값이 VPVR에서는 실제 워밍업
길이와 정확히 일치한다.

### 2. 지표 등록

`engine/indicators/__init__.py` — import 줄에 `create_vpvr_poc, create_vpvr_vah, create_vpvr_val`
추가, `INDICATOR_FACTORY`에 아래 3개 추가(`PIVOT_S1` 근처, 가격대 계열 지표들 옆):
```python
    "VPVR_POC": create_vpvr_poc,
    "VPVR_VAH": create_vpvr_vah,
    "VPVR_VAL": create_vpvr_val,
```

`engine/condition_tree.py`의 `get_indicator_value()`에 `PIVOT_P`/`R1`/`S1`과 동일한 패턴으로 분기
추가:
```python
    elif indicator_name == "VPVR_POC":
        return float(obj.lines.poc[0])
    elif indicator_name == "VPVR_VAH":
        return float(obj.lines.vah[0])
    elif indicator_name == "VPVR_VAL":
        return float(obj.lines.val[0])
```

`AUX_MARKET_INDICATORS`(`engine/condition_tree.py`), `_OPTIONAL_LINE_CANDIDATES`(`engine/runner.py`)
**둘 다 수정 불필요** — VPVR은 대상 코인 자신의 기본 캔들 데이터(`high`, `low`, `volume`)만 쓰고
보조 마켓도 외부 데이터도 필요 없다.

`backend/main.py`는 카탈로그 등록 3줄만 추가한다(아래) — 병합 로직도, import도 필요 없다.

### 3. 카탈로그 / 조건 빌더 / 가이드 탭

- **`backend/main.py`의 `INDICATOR_CATALOG`**: 카테고리는 기존 **"가격대"**를 재사용한다(FIB/Pivot
  Points가 이미 여기 있고, VPVR도 "가격 수준을 미리 계산해두는" 같은 성격의 지표라 자연스럽다 —
  `frontend/lib/indicator-categories.ts` 수정 불필요). 3개 항목을 `PIVOT_S1` 다음에 추가한다(정확한
  설명 문구는 구현 플랜에서 확정).
- **`StrategyConditionBuilder.tsx`의 `PRICE_SCALE_INDICATORS`**: `VPVR_POC`, `VPVR_VAH`, `VPVR_VAL`
  3개를 추가한다 — 셋 다 절대 가격값이라(오실레이터가 아님) `SMA`/`FIB_*`/`PIVOT_*`와 같은 그룹에
  속하고, threshold 자동 추천값은 "현재가"가 된다(`OSCILLATOR_BOUNDS`가 아니라 `PRICE_SCALE_INDICATORS`
  로 감).
- **가이드 탭**: 기존 Pivot Points 패턴대로 — `guide-sample-data.ts`에 합성 POC/VAH/VAL 시계열,
  `indicator-guide.ts`에 공식/의미/사용법, `indicator-example-builder.ts`에 종가 위에 POC/VAH/VAL
  3개 라인을 겹쳐 그리는 라인 차트(새 히스토그램 시각화는 만들지 않음, 기존 recharts 라인 차트
  재사용 — Pivot Points의 `case 'PIVOT_P': case 'PIVOT_R1': case 'PIVOT_S1':` 케이스와 동일한 모양).

### 4. 테스트 전략

- **`engine/indicators`**: `NUM_BINS`이 모듈 상수라 손으로 추적 가능한 테스트에서는 작은 값(예: 4)으로
  monkeypatch해서 검증하고, 실제 운영값(24)은 건드리지 않는다. 케이스:
  1. 워밍업 중(`period`개 미만 봉) 세 라인 모두 NaN.
  2. 작은 `NUM_BINS`(예: 4)로 hand-traced POC/VAH/VAL — 통제된 고가/저가/거래량 시퀀스로 bin별
     누적 거래량을 손으로 계산해 기대값과 비교(VPIN의 8봉 손 추적 테스트와 같은 방식).
  3. 완전히 평평한 가격(`window_high == window_low`)에서 0으로 나누기 없이 POC=VAH=VAL=그 가격으로
     처리되는지.
  4. 윈도우 안에 도지 봉(`h == l`인 개별 봉)이 섞여 있어도 크래시 없이 처리되는지.
  5. 기본 설정(`NUM_BINS=24`, `period=50`)으로 실행했을 때 항상 `VAL <= POC <= VAH`이고 셋 다
     윈도우 범위(`window_low`~`window_high`) 안에 있는지 스모크 검증(정확한 값이 아니라 불변식
     검증).
  6. `INDICATOR_FACTORY`에 3개 다 등록돼 있고 `make_oscillating_df` 스모크 테스트로 크래시 없이
     값을 내는지.
- **`backend/main.py`**: 기존 `test_get_indicator_catalog_covers_all_registered_indicators`가 카탈로그
  등록 누락을 자동으로 잡아준다(VPIN 때도 이 테스트가 실제로 이 역할을 했음) — 새 백엔드 전용 테스트
  불필요.
- **프론트**: `npx tsc --noEmit` + Playwright로 조건 빌더 "가격대" 카테고리에 3개 지표가 뜨고
  threshold가 현재가로 자동 채워지는지, `/guide`에서 라인 차트가 렌더되는지 확인(이 저장소는 프론트
  유닛테스트 인프라가 없다는 기존 컨벤션 그대로).

## 이 스펙에 포함하지 않은 것

- **VPVR을 이용한 "가격이 Value Area 안/밖에 있는지"(불리언) 신호**: 조건 빌더의 `ConditionBlock`은
  지표 값을 고정 숫자 threshold와만 비교하는 구조라(`{indicator, params, operator, threshold}`),
  "종가가 VAL과 VAH 사이인지" 같은 두 지표 간 비교는 애초에 이 아키텍처에서 지원하지 않는다 — PIVOT
  계열도 동일한 제약을 이미 받아들이고 있으므로 이 스펙에서 새로 풀지 않는다.
- **Low Volume Node(LVN) 탐지**: 거래량이 특히 적은 구간을 별도로 짚어내는 건 POC/VAH/VAL과는
  다른 산출물이라 범위 밖.
- **세션/일 단위 앵커링**: 실제 트레이딩 툴의 Volume Profile은 흔히 "장 시작"이나 "자정" 같은 고정
  시점에 리셋되는 세션 프로파일을 쓰지만, 이 프로젝트는 단일 timeframe 캔들 구조라 순수 롤링 윈도우로
  근사한다(VPIN/상관계수 지표들과 같은 결정).
- **VPVR의 실전 예측력 검증**: 이 스펙은 계산 방법론을 올바르게 구현하는 것까지만 다룬다.
