# VPIN(주문흐름 독성도) 지표 설계

## 목적

`docs/superpowers/specs/2026-07-27-strategy-source-classification.md`의 "선행지표류" B 레이어 중
하나인 VPIN(Volume-Synchronized Probability of Informed Trading)을 조건 빌더에 새 지표로 추가한다.
[[upbit-v1-external-indicator-roadmap]]에서 정한 순서(체결강도 → VPIN → Volume Profile)의 두 번째
항목이다. 체결강도(Volume Power)는 원시 체결 틱 API(`/v1/trades/ticks`)의 7일 조회 제한 때문에
드롭했지만, VPIN은 아래 리서치에서 확인했듯 **원시 틱 데이터가 필요 없어 이 제약에서 자유롭다.**

## 배경 리서치

### 분류 문서의 부정확했던 전제를 정정

`2026-07-27-strategy-source-classification.md`는 VPIN을 "데이터 소스는 체결강도와 같은 체결 틱"이라고
적어뒀으나, 이건 검증 없이 넘겨짚은 것이었다. 실제로 웹서치로 확인한 VPIN의 진짜 방법론(Easley,
López de Prado, O'Hara)은:

- 최초 논문들은 틱 단위 매수/매도 분류(tick rule)를 썼지만, **저자들 스스로 2012년 이후 논문에서
  "Bulk Volume Classification(BVC)"을 표준으로 제시**했다. BVC는 캔들(바)의 가격 변화만으로 그 구간
  거래량 중 매수/매도 비율을 확률적으로 추정하는 방식이라 **틱 단위 원본 데이터가 전혀 필요 없다.**
- 출처: [quantresearch.org/VPIN.pdf](https://www.quantresearch.org/VPIN.pdf),
  [Bulk Volume Classification and Information Detection](https://www.researchgate.net/publication/332405528_Bulk_Volume_Classification_and_Information_Detection).
- 이 프로젝트는 캔들(종가+거래량)을 이미 `get_candles()`로 받아오고 있으므로, **VPIN 계산에 새로운
  외부 데이터 수집이 전혀 필요 없다.** 이 점이 이 스펙의 아키텍처를 fear-greed/한국프리미엄과
  근본적으로 다르게 만든다(아래 "검토한 접근" 참고).

### 왜 backtrader 라이브 지표로 가는가 (fear-greed/한국프리미엄 패턴을 안 쓰는 이유)

초안에서는 `FEAR_GREED_CMC`/`KOREA_PREMIUM`처럼 "백엔드 병합 단계에서 pandas로 미리 계산해 컬럼 하나로
병합"하는 패턴을 검토했으나, 스펙 작성 직전에 결함을 발견해 기각했다: **VPIN은 `period` 파라미터를
갖는데, 조건 트리 안에서 서로 다른 `period` 값을 가진 VPIN 블록이 동시에 쓰일 수 있다**(예:
`period=20`짜리와 `period=14`짜리를 각각 매수/매도 조건에). 컬럼 하나짜리 병합으로는 이걸 표현할 수
없다. `BTC_CORRELATION`/`USDT_CORRELATION`이 이미 `RollingCorrelation`이라는 커스텀 `bt.Indicator`로
이 문제(같은 지표, 다른 파라미터 조합마다 별도 인스턴스)를 자연스럽게 해결하고 있으므로, VPIN도 같은
패턴을 따른다.

## 아키텍처

### 검토한 접근

- **A. 백엔드 병합 단계에서 pandas로 사전 계산 (기각)** — `period` 파라미터를 표현할 수 없어 기각
  (위 리서치 참고).
- **B. 커스텀 `bt.Indicator`, backtrader 라이브 계산 (채택)** — `engine/indicators/market.py`의
  `RollingCorrelation`, `engine/indicators/volume.py`의 `OBV`와 완전히 같은 패턴. 대상 코인 자신의
  `data.close`/`data.volume`만 쓰고 다른 마켓 캔들도, 외부 API도, 백엔드 병합 로직도 전혀 필요 없다
  — 이번 C/B 레이어 확장 중 백엔드 통합이 가장 단순한 케이스가 된다(`AUX_MARKET_INDICATORS` 등록도
  불필요, `backend/main.py` 수정 자체가 없음).

### 계산 알고리즘

1. **거래량 버킷(volume bar) 나누기**: 캔들을 순서대로 누적하다가, 누적 거래량이 "그 시점까지의 최근
   `period`개 캔들 평균 거래량"(롤링, 매 봉마다 갱신 — look-ahead 방지)에 도달하면 버킷 하나를 완성
   하고 리셋한다.
2. **BVC로 버킷별 매수/매도 추정**:
   ```
   z_i = (버킷_i 종가 − 버킷_{i-1} 종가) / (최근 period개 버킷 가격변화의 표준편차)
   매수추정량_i = 버킷_i 거래량 × Φ(z_i)        (Φ = 표준정규분포 누적분포함수)
   매도추정량_i = 버킷_i 거래량 − 매수추정량_i
   ```
   `Φ`는 Python 표준 라이브러리 `statistics.NormalDist().cdf()`로 계산한다(실측 확인:
   `NormalDist().cdf(0.0) == 0.5`, `NormalDist().cdf(1.0) ≈ 0.8413` — scipy 등 새 의존성 불필요).
3. **VPIN 값**: 최근 `period`개 버킷의 `|매수추정량 − 매도추정량| / 버킷거래량` 평균. 0~1 범위이며,
   1에 가까울수록 그 구간 주문흐름이 한쪽으로 강하게 쏠렸다는(=정보거래/독성 흐름 강함) 뜻이다.
4. **원래 캔들에 값 이어붙이기(forward-fill)**: 버킷이 아직 안 끝난 봉은 직전에 완성된 버킷의 VPIN
   값을 그대로 들고 있는다 — `OBV`가 `self.lines.obv[0] = self.lines.obv[-1]`로 변화 없을 때 직전
   값을 유지하는 것과 동일한 패턴. backtrader의 봉 단위 `next()` 루프 안에서 자연스럽게 표현되므로,
   fear-greed의 날짜경계 `merge_asof`처럼 별도 병합 단계가 필요 없다.
5. **워밍업/엣지 케이스**:
   - 표준편차가 0이거나(버킷 2개 미만) 정의되지 않으면 `z=0`(매수/매도 50:50, "정보 없음"으로 간주)
     — `RollingCorrelation`이 분산 0일 때 상관계수 0.0을 반환하는 것과 같은 방어 패턴.
   - 완성된 버킷이 아직 `period`개 미만이면 그 구간은 `NaN`을 낸다(조건 평가 시 자연히 False로
     처리됨 — `RSI`/`SMA` 등 다른 워밍업 필요 지표들과 동일).

### 파라미터

`period` 하나(기본값 20, `BTC_CORRELATION`/`USDT_CORRELATION`과 동일한 기본값 관례)로 버킷 크기
계산용 캔들 개수와 VPIN 평균 낼 버킷 개수를 동시에 제어한다 — 이 프로젝트의 다른 지표들이 전부
파라미터 1개(`period`)만 쓰는 관례를 그대로 따른다(2파라미터 지표는 MACD뿐이고 그건 두 라인을 같이
내는 특수 케이스).

## 상세 설계

### 1. 지표 구현 — `engine/indicators/volume.py` (기존 파일에 추가)

거래량 기반 지표들이 이미 모여있는 파일이라 여기에 추가한다(새 파일 불필요).

```python
import statistics
from collections import deque


class VolumeBarVPIN(bt.Indicator):
    """거래량 버킷(volume bar) 기반 VPIN. Easley/López de Prado/O'Hara의 Bulk Volume
    Classification(BVC, 2012)을 따른다 — 틱 단위 매수/매도 라벨이 필요 없고, 캔들
    종가·거래량만으로 버킷별 매수/매도 비율을 확률적으로 추정한다."""

    lines = ("vpin",)
    params = (("period", 20),)

    def __init__(self) -> None:
        period = self.p.period
        self._recent_volumes: deque = deque(maxlen=period)
        self._bucket_cum_volume = 0.0
        self._last_bucket_close: float | None = None
        self._bucket_deltas: deque = deque(maxlen=period)
        self._bucket_imbalance_ratios: deque = deque(maxlen=period)
        self.addminperiod(period + 1)

    def _accumulate(self) -> None:
        """이번 봉을 현재 버킷에 누적하고, 목표 거래량에 도달했으면 버킷을 완성해
        BVC로 매수/매도 불균형 비율을 계산·기록한다. next()/nextstart() 공통 로직."""
        period = self.p.period
        self._recent_volumes.append(self.data.volume[0])
        self._bucket_cum_volume += self.data.volume[0]

        target = (
            statistics.mean(self._recent_volumes)
            if len(self._recent_volumes) == period
            else None
        )
        if target is None or self._bucket_cum_volume < target:
            return

        bucket_close = self.data.close[0]
        bucket_volume = self._bucket_cum_volume

        if self._last_bucket_close is not None:
            delta = bucket_close - self._last_bucket_close
            self._bucket_deltas.append(delta)
            sigma = statistics.stdev(self._bucket_deltas) if len(self._bucket_deltas) >= 2 else 0.0
            z = delta / sigma if sigma > 0 else 0.0
            buy_ratio = statistics.NormalDist().cdf(z)
            buy_volume = bucket_volume * buy_ratio
            sell_volume = bucket_volume - buy_volume
            imbalance_ratio = abs(buy_volume - sell_volume) / bucket_volume if bucket_volume else 0.0
            self._bucket_imbalance_ratios.append(imbalance_ratio)

        self._last_bucket_close = bucket_close
        self._bucket_cum_volume = 0.0

    def nextstart(self) -> None:
        # next()가 처음 호출되는 시점(addminperiod로 이미 period+1봉은 지난 상태)에도
        # 이 지표 자신의 라인은 이번이 첫 기록이라 vpin[-1]이 정의돼 있지 않다.
        # OBV의 nextstart()와 같은 이유로, 첫 호출만 [-1] 참조 없이 따로 처리한다.
        self._accumulate()
        if len(self._bucket_imbalance_ratios) == self.p.period:
            self.lines.vpin[0] = statistics.mean(self._bucket_imbalance_ratios)
        else:
            self.lines.vpin[0] = float("nan")

    def next(self) -> None:
        self._accumulate()
        if len(self._bucket_imbalance_ratios) == self.p.period:
            self.lines.vpin[0] = statistics.mean(self._bucket_imbalance_ratios)
        else:
            self.lines.vpin[0] = self.lines.vpin[-1]


def create_vpin(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return VolumeBarVPIN(data, period=period)
```

**초안에서 발견해 고친 버그**: 처음엔 `next()` 안에서 `len(self) > 1`로 "첫 호출인지"를 구분하려
했으나, `addminperiod(period+1)` 때문에 `next()`는 이미 `len(self) >= period+1`인 시점부터 호출되므로
이 조건은 사실상 항상 참이 되어 의미가 없었다 — 그 시점에도 **이 지표 자신의 `vpin` 라인은 이번이
첫 기록**이라 `self.lines.vpin[-1]`이 아직 정의돼 있지 않다. `OBV`가 정확히 같은 이유로
`nextstart()`(첫 호출 전용, `[-1]` 참조 없이 처리)와 `next()`(이후 호출, `[-1]` 참조 가능)를
분리하는 것과 동일한 패턴으로 고쳤다.

### 2. 지표 등록

`engine/indicators/__init__.py` — import 줄에 `create_vpin` 추가, `INDICATOR_FACTORY`에
`"VPIN": create_vpin,` 추가(`OBV`/`VOLUME_SMA` 근처, 거래량 계열 지표들 옆).

`engine/condition_tree.py`의 `AUX_MARKET_INDICATORS`, `engine/runner.py`의
`_OPTIONAL_LINE_CANDIDATES` **둘 다 수정 불필요** — VPIN은 대상 코인 자신의 기본 캔들 데이터
(`close`, `volume`)만 쓰고 보조 마켓도 외부 데이터도 필요 없다.

`get_indicator_value()`(`engine/condition_tree.py`)도 수정 불필요 — `VolumeBarVPIN`은 단일 라인
(`vpin`)이라 기존 fallback(`float(obj[0])`)이 `RollingCorrelation`/`OBV`와 마찬가지로 그대로 처리한다.

`backend/main.py`는 **아무것도 수정하지 않는다** — 병합 로직도, import도 필요 없다. 카탈로그 등록만
추가한다(아래).

### 3. 카탈로그 / 조건 빌더 / 가이드 탭

- **`backend/main.py`의 `INDICATOR_CATALOG`**: 카테고리는 신규 카테고리가 필요하다 — 기존
  "거래량"(단순 수량 지표들)이나 "시장 심리"(외부/교차마켓 지표들) 어디에도 딱 맞지 않는다. **"거래량"
  카테고리를 그대로 재사용**하기로 한다(주문흐름 불균형도 결국 거래량 기반 지표이고,
  `TRADE_VALUE`/`VOLUME_SMA`/`OBV`가 이미 여기 있어 "거래량 계열의 더 정교한 버전"이라는 위치가
  자연스럽다 — 새 카테고리를 만들면 프론트 `indicator-categories.ts`에 아이콘/색상도 새로 정의해야
  해서 배보다 배꼽이 커진다).
  ```python
  {
      "value": "VPIN", "label": "VPIN (주문흐름 독성도)", "category": "거래량",
      "params": [{"key": "period", "label": "기간", "default": 20}],
      "description": "거래량 버킷 단위로 매수/매도 주문 불균형을 추정한 값(0~1)입니다. 1에 가까울수록 그 구간 거래가 한쪽(매수 또는 매도)으로 강하게 쏠렸다는 뜻으로, 급등락 직전의 정보거래(독성 주문흐름) 징후로 해석합니다. 틱 데이터가 아니라 캔들 가격 변화로 매수/매도 비율을 확률적으로 추정하는 방식(Bulk Volume Classification)을 씁니다.",
      "example": "period=20, 연산자 >, 임계값 0.4면: 최근 20개 거래량 버킷 동안 주문흐름 불균형이 뚜렷한(변동성 폭발 전조로 흔히 해석되는) 구간을 포착합니다.",
  },
  ```
- **`StrategyConditionBuilder.tsx`의 `OSCILLATOR_BOUNDS`**: VPIN은 0~1 범위 오실레이터라 이 패턴에
  맞는다. `{ low: 0.2, high: 0.4 }` 추가(학계에서 흔히 인용되는 "완만~과열" 구간의 대략적인 눈대중값 —
  RSI의 30/70처럼 정답은 아니고 사용자가 직접 조정하는 걸 전제한 초기값).
- **가이드 탭**: 기존 패턴대로 `guide-sample-data.ts`에 합성 VPIN 시계열, `indicator-guide.ts`에
  공식/의미/사용법, `indicator-example-builder.ts`에 표+게이지(0~1, 0.2/0.4 구간 색상 구분) 추가.

### 4. 테스트 전략

- **`engine/indicators`**: 합성 OHLCV로 (1) 버킷이 실제로 목표 거래량에 도달할 때 완성되는지(작은
  `period`와 통제된 거래량 시퀀스로 버킷 경계를 손으로 계산해 검증), (2) 가격이 계속 상승하는 버킷은
  매수 비율이 50%보다 높게 추정되는지(`z > 0` → `Φ(z) > 0.5`), 하락 버킷은 반대인지, (3) 버킷 미완성
  구간에서 직전 값을 그대로 유지하는지(forward-fill), (4) 워밍업 전(`period`개 버킷 미완성)엔 NaN인지,
  (5) 가격변화 표준편차가 0인 초기 구간에서 `ZeroDivisionError` 없이 `z=0`으로 처리되는지.
- **`backend/main.py`**: 수정 사항이 없으므로 새 백엔드 테스트 불필요 — 기존
  `test_get_indicator_catalog_covers_all_registered_indicators`가 카탈로그 등록 누락을 자동으로
  잡아준다(이번 세션에서 이미 한 번 실제로 이 테스트가 이 역할을 했다).
- **프론트**: `npx tsc --noEmit` + Playwright로 조건 빌더 "거래량" 카테고리에 "VPIN"이 뜨는지,
  `/guide`에서 표+게이지가 렌더되는지 확인(이 저장소는 프론트 유닛테스트 인프라가 없다는 기존
  컨벤션 그대로).

## 이 스펙에 포함하지 않은 것

- **체결강도(Volume Power)**: 이미 드롭 결정됨([[upbit-v1-external-indicator-roadmap]] 참고, 업비트
  틱 조회 API 7일 제한 때문).
- **Volume Profile(VPVR)**: 로드맵상 다음 순서. VPIN과 달리 "가격대별 거래량 분포"를 다루는 전혀 다른
  계산이라 별도 스펙이 필요하다.
- **VPIN의 "정보거래 신호로서의 정확도" 검증**: BVC는 학계에서도 논쟁이 있는 근사 방법(리서치 절 참고 —
  "변동성이 오르면 BVC 분류 오류가 체계적으로 늘어난다"는 비판도 있음). 이 스펙은 계산 방법론을
  올바르게 구현하는 것까지만 다루고, 실제로 이 지표가 백테스트 수익률을 개선하는지는 사용자가 직접
  전략에 넣어보고 판단할 몫이다.
