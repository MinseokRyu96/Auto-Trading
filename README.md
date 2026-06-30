# GoStop Auto-Trading

한국투자증권 Open API를 이용해 국내주식 데이터를 수집하고, 퀀트 전략으로 후보를 고른 뒤, 대시보드와 텔레그램으로 운영 상태를 확인할 수 있는 자동매매 시스템입니다.

현재 구현은 실전 운영을 전제로 하지만, 실주문은 반드시 환경변수와 실행 옵션이 동시에 켜져야 동작하도록 설계했습니다.

## 주요 기능

- 한국투자증권 Open API 연동
- 계좌 잔고, 예수금, 현재가, 일봉, 거래대금 랭킹, 시가총액 랭킹 수집
- Google News RSS 기반 종목 뉴스 수집
- 유동성, 모멘텀, 변동성, 뉴스 점수를 결합한 매수 후보 선정
- 하락장 방어용 리스크 오버레이
- 소액 계좌용 정수 주식 수량 배분
- 잔여 예산을 뉴스 기반 후보에 추가 배정
- 매수/매도 주문 근거 생성
- 매수 직후 잦은 매도를 막는 최소 보유 기간 가드
- 대시보드에서 계좌, 주문, 후보, 리스크, 손익, 학습 루프 확인
- 대시보드에서 자동매매 ON/OFF 제어
- 텔레그램 주문/오류/장중 업데이트 알림
- 장 마감 후 성과 리뷰와 파라미터 개선 제안 저장

## 프로젝트 구조

```text
GoStop
├── config/
│   ├── symbols.csv                         # 기본 관심 종목
│   └── launchd/                            # macOS LaunchAgent 예시
├── docs/
│   ├── data_collection_plan.md             # 데이터 수집 설계
│   └── trading_strategy_research.md        # 전략 리서치 요약
├── scripts/
│   ├── run_dashboard.sh
│   └── run_market_runner.sh
├── src/gostop/
│   ├── cli.py                              # CLI 엔트리포인트
│   ├── collector.py                        # KIS 데이터 수집
│   ├── config.py                           # 환경변수 설정
│   ├── dashboard.py                        # 대시보드 API 서버
│   ├── dashboard_static/                   # HTML/CSS/JS 대시보드 UI
│   ├── eod_review.py                       # 장 마감 리뷰/학습 루프
│   ├── execution.py                        # 주문 제출/가드
│   ├── kis_client.py                       # KIS HTTP 클라이언트
│   ├── news.py                             # 뉴스 수집/감성 점수
│   ├── notify.py                           # 텔레그램 알림
│   ├── runner.py                           # 장중 자동 실행 러너
│   ├── storage.py                          # SQLite 저장소
│   └── strategy.py                         # 매매 전략
├── .env.example
├── pyproject.toml
└── README.md
```

## 설치와 초기 설정

```bash
cp .env.example .env
PYTHONPATH=src python3 -m gostop.cli init-db
```

`.env`에는 한국투자증권 API 키, 계좌번호, 텔레그램 토큰 등을 직접 입력합니다.

주의:

- `.env`는 절대 커밋하지 않습니다.
- `data/*.sqlite3`, `data/kis_token.json`, 로그 파일도 커밋하지 않습니다.
- 실주문 전에는 반드시 모의 실행과 대시보드 값을 먼저 확인해야 합니다.

## 핵심 환경변수

```bash
KIS_ENV=demo
KIS_APP_KEY=your_app_key
KIS_APP_SECRET=your_app_secret
KIS_ACCOUNT_NO=

GOSTOP_DB_PATH=data/gostop.sqlite3
GOSTOP_MARKET_DIV=J

GOSTOP_ALLOW_LIVE_TRADING=false
GOSTOP_MAX_DAILY_ORDER_VALUE=1000000
GOSTOP_MAX_SINGLE_ORDER_VALUE=1000000
GOSTOP_KIS_MIN_INTERVAL_SECONDS=1.05
GOSTOP_KIS_REQUEST_TIMEOUT_SECONDS=30

GOSTOP_NEWS_ENABLED=true
GOSTOP_NEWS_REFRESH_MINUTES=30
GOSTOP_NEWS_MAX_SYMBOL_QUERIES=12
GOSTOP_NEWS_ITEMS_PER_QUERY=12

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

실주문은 다음 조건을 모두 만족해야 합니다.

- `KIS_ENV=real`
- `GOSTOP_ALLOW_LIVE_TRADING=true`
- CLI 실행 시 `--live --confirm-live` 사용
- 대시보드의 매매 기능이 ON

## 데이터 수집 로직

`collector.py`는 한국투자증권 Open API에서 다음 데이터를 수집합니다.

| 데이터 | 목적 |
| --- | --- |
| 현재가 | 주문 가격, 후보 가격, 대시보드 최신 시세 |
| 일봉 OHLCV | 20/60일 모멘텀, 변동성, 거래대금 계산 |
| 거래대금 랭킹 | 유동성 높은 후보 탐색 |
| 시가총액 랭킹 | 초대형주/중소형주 구분 |
| 계좌 잔고 | 현재 보유수량, 평가금액, 예수금 동기화 |
| 휴장일 | 장중 러너 실행 여부 판단 |
| 원본 API 응답 | 디버깅과 추적용 저장 |

주요 명령:

```bash
PYTHONPATH=src python3 -m gostop.cli collect-daily --symbols 005930,000660 --start 20250101
PYTHONPATH=src python3 -m gostop.cli collect-current --symbols 005930,000660
PYTHONPATH=src python3 -m gostop.cli collect-volume-rank
PYTHONPATH=src python3 -m gostop.cli collect-market-cap
PYTHONPATH=src python3 -m gostop.cli collect-balance
PYTHONPATH=src python3 -m gostop.cli collect-news
```

## 뉴스 수집 로직

`news.py`는 Google News RSS를 이용해 국내 증시와 종목별 뉴스를 수집합니다.

뉴스 수집 우선순위:

1. 현재 보유 종목
2. 현재 가격이 1,000원 이상 100,000원 이하인 매수 가능 후보
3. 최신 전략 후보
4. 최신 거래대금/시가총액 랭킹 종목
5. `config/symbols.csv` 기본 관심 종목

ETF, 레버리지, 인버스, 선물, ETN, 우선주는 뉴스 기반 매수 대상에서 제외합니다.

뉴스 점수는 단순 키워드 기반입니다.

- 긍정 키워드 예: 호실적, 상승, 강세, 급등, 신고가, 수주, 계약, 승인, 배당, 자사주
- 부정 키워드 예: 실적 부진, 적자, 하락, 약세, 급락, 목표가 하향, 소송, 제재, 유상증자

뉴스 점수는 매수 후보 점수에 가산되고, 소액 계좌의 잔여 예산을 사용할 때 별도 후보군으로 활용됩니다.

## 매매 전략

현재 전략명은 `liquidity_momentum_v1`입니다.

전략의 기본 방향은 초단타가 아니라 다음 조합입니다.

```text
유동성 필터
→ 20/60일 모멘텀
→ 변동성 제한
→ 시가총액 쏠림 완화
→ 뉴스 점수 반영
→ 하락장 리스크 오버레이
→ 정수 주식 수량으로 목표 비중 배분
```

### 1. 유니버스

전략 후보는 다음 데이터에서 구성됩니다.

- 거래대금 랭킹
- 시가총액 랭킹
- `config/symbols.csv` 관심 종목
- 최근 뉴스 수집 대상
- 현재 보유 종목

### 2. 기본 필터

종목은 다음 조건을 통과해야 합니다.

- 종목코드가 정상 숫자 형식
- 우선주 제외
- ETF/ETN/레버리지/인버스/선물 관련 종목 제외
- 현재가 1,000원 이상
- 최근 20거래일 평균 거래대금 10억원 이상
- 20일 수익률 양수
- 60일 수익률 양수
- 연율화 변동성 85% 이하

### 3. 점수 계산

기본 점수는 다음 비중으로 계산합니다.

| 항목 | 비중 |
| --- | ---: |
| 60일 모멘텀 | 40% |
| 20일 모멘텀 | 30% |
| 거래대금 순위 | 10% |
| 낮은 변동성 | 10% |
| 대형주 쏠림 완화 | 10% |

뉴스가 있는 종목은 뉴스 감성 점수를 추가로 반영합니다.

### 4. 포트폴리오 구성

기본 구성:

- 최대 보유 종목: 5개
- 코어 포지션: 3개
- 탐색 포지션: 2개
- 시가총액 20위 이내 초대형주 최대 2개
- 일반 계좌 총 주식 노출 최대 70%
- 일반 계좌 종목당 최대 비중 20%

소액 계좌 구성:

- 계좌 평가금액 100만원 이하를 소액 계좌로 취급
- 비중보다 실제 정수 주식 수량을 우선
- 살 수 없는 고가 후보는 제외
- 현금 버퍼는 0.2% 수준만 남기고 가능한 예산을 사용
- 최소 주문 금액은 5,000원으로 완화

## 뉴스 기반 잔여 예산 매수

일반 모멘텀 후보만 사용하면, 소액 계좌에서는 고가 종목 때문에 남는 예산이 생깁니다.

이를 보완하기 위해 `news_sweep` 후보군을 추가했습니다.

작동 조건:

- 소액 계좌일 때만 활성화
- 시장 국면이 `crash`가 아닐 것
- 남은 예산으로 1주 이상 매수 가능할 것
- 뉴스 감성 점수가 최소 기준 이상일 것
- 최근 뉴스 건수가 최소 기준 이상일 것
- ETF/우선주/레버리지/인버스 등은 제외

주문 사유 예시:

```text
뉴스 잔여예산 슬롯; 남은 예산으로 1주 이상 매수 가능;
뉴스 12건 점수 +0.25 - 긍정: 상승;
시장 리스크 risk_off - 시장 프록시 추세 약화 또는 손실 구간
```

## 하락장 리스크 관리

전략은 수집된 종목들의 동일가중 일별 수익률로 시장 프록시를 만듭니다.

| 국면 | 조건 | 목표 노출 |
| --- | --- | ---: |
| `risk_on` | 추세와 변동성 정상 | 100% 계수 |
| `risk_off` | 20일 수익률 약화 또는 60일 낙폭 확대 | 35% |
| `high_volatility` | 20일 변동성 35% 이상 | 50% |
| `crash` | 20일 급락 또는 60일 큰 낙폭 | 0% |
| `unknown` | 데이터 부족 | 35% |

`crash`에서는 신규 목표 노출을 0으로 낮춰 방어합니다.

## 매도 로직

매도는 다음 상황에서 발생합니다.

1. 보유 종목이 최신 전략 후보에서 제외됨
2. 보유 평가액이 목표 평가액보다 큼
3. 시장 리스크 국면으로 목표 노출이 축소됨
4. `crash` 국면에서 방어적으로 목표 노출이 0에 가까워짐

매도 근거는 주문 이벤트와 텔레그램에 함께 기록됩니다.

예시:

```text
전략 매도 후보: 현재 선정 종목에서 제외;
제외 사유: 20/60일 모멘텀이 양수가 아님;
시장 리스크 risk_off - 시장 프록시 추세 약화 또는 손실 구간
```

또는:

```text
목표 비중 축소: 현재 평가액 120,000원 > 목표 평가액 80,000원;
코어 슬롯; 20일 수익률 5.12%, 60일 수익률 18.40%, 거래대금/변동성 필터 통과
```

## 과도한 단기 매매 방지

뉴스 기반 매매는 후보가 빠르게 바뀔 수 있기 때문에, 매수 직후 바로 매도되는 문제가 생길 수 있습니다.

이를 막기 위해 최소 보유 기간 가드를 구현했습니다.

- 기본 최소 보유 기간: 2일
- 매수 주문이 `submitted`로 기록된 시점을 기준으로 계산
- 2일이 지나기 전에는 매도 후보에서 제외
- 단, 시장 국면이 `crash`이면 즉시 매도 가능

이 로직은 수수료와 스프레드 비용으로 인한 잦은 손실을 줄이기 위한 안전장치입니다.

## 주문 실행 로직

`execution.py`는 최신 리밸런싱 플랜을 읽어 주문을 제출합니다.

주문 방식:

- 국내주식 현금 지정가 주문
- 매수 TR: 실전 `TTTC0012U`, 모의 `VTTC0012U`
- 매도 TR: 실전 `TTTC0011U`, 모의 `VTTC0011U`
- 시장가 주문은 사용하지 않음

주문 가드:

- 실주문 환경변수와 `--confirm-live` 둘 다 필요
- 대시보드 매매 ON 상태 필요
- 당일 동일 종목/동일 방향 중복 주문 차단
- 1회 주문 한도 초과 차단
- 일일 주문 한도 초과 차단
- 주문 실패 시 `order_events`에 실패 상태 기록

## 장중 자동매매 러너

`runner.py`는 장중에 반복 실행되는 자동매매 루프입니다.

기본 흐름:

```text
장 운영 여부 확인
→ 계좌 잔고 수집
→ 현재가 수집
→ 일정 주기마다 랭킹/뉴스 수집
→ 전략 실행
→ 주문 후보 생성
→ 실주문 또는 dry-run 실행
→ 텔레그램 알림
→ 다음 주기까지 대기
```

예시:

```bash
PYTHONPATH=src python3 -m gostop.cli market-runner \
  --live \
  --confirm-live \
  --interval-minutes 5 \
  --start-time 09:00 \
  --end-time 15:20
```

macOS에서는 `config/launchd/`의 plist를 참고해 항상 켜지는 LaunchAgent로 운영할 수 있습니다.

## 대시보드

대시보드는 `http://127.0.0.1:8765`에서 실행됩니다.

```bash
PYTHONPATH=src python3 -m gostop.cli dashboard
```

대시보드 기능:

- Market Regime: 현재 시장 리스크 국면
- Target Exposure: 리스크 오버레이 반영 목표 노출
- Cash: 예수금
- Total Equity: 계좌 총평가금액
- Realized P&L: 실현손익 또는 주문 이벤트 기반 추정 실현손익
- Order Queue: 최신 주문 후보 수
- 전략 후보: 종목별 점수, 목표비중, 선정/관찰/축소 사유
- 리밸런싱 미리보기: 매수/매도 예상 주문과 근거
- 손익 추이: 일별 실현손익
- 평가금액: 계좌 총평가금액 시계열
- 주문 이벤트: 제출, 실패, 스킵 상태와 근거
- 체결 내역: 실제 체결 또는 주문 이벤트 기반 추정 체결
- 보유 종목: 최신 잔고 스냅샷
- 최신 시세: 현재가와 등락률
- 거래대금/시가총액 랭킹
- 장 마감 회고와 추천 파라미터

## Realized P&L 계산

한국투자증권 체결 내역을 별도로 저장하지 못한 경우, 대시보드는 `order_events`의 `submitted` 주문을 이용해 실현손익을 추정합니다.

추정 방식:

1. 당일 `submitted` 매수 주문을 FIFO 매수 lot으로 저장
2. 당일 `submitted` 매도 주문이 나오면 이전 매수 lot과 매칭
3. `매도금액 - 매수원가`를 실현손익으로 계산
4. 실제 `trade_executions` 데이터가 있으면 실제 체결 데이터를 우선 사용

대시보드에는 추정치일 때 `추정 체결`, `추정 승률`로 표시됩니다.

## 장 마감 학습 루프

`eod_review.py`는 하루 매매가 끝난 뒤 데이터를 다시 분석합니다.

```bash
PYTHONPATH=src python3 -m gostop.cli end-of-day-review
```

하는 일:

- 최신 계좌/시세/전략 데이터 수집
- 당일 실현손익과 계좌 변화 계산
- 전략 후보와 관찰 후보의 성과 비교
- 시장 리스크 국면 검토
- 전략 품질 점수 저장
- 파라미터 조정 추천 생성

추천 파라미터는 자동으로 적용되지 않습니다. 명시적으로 옵션을 켜야 적용됩니다.

```bash
PYTHONPATH=src python3 -m gostop.cli end-of-day-review --apply-suggestions --min-confidence 0.70
```

## 텔레그램 알림

`.env`에 텔레그램 봇 토큰과 chat id를 설정하면 알림을 받을 수 있습니다.

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

테스트:

```bash
PYTHONPATH=src python3 -m gostop.cli test-telegram --message "[GoStop] test"
```

알림 종류:

- 자동매매 러너 시작
- 장중 업데이트
- 매수 주문 제출/실패/스킵
- 매도 주문 제출/실패/스킵
- 주문별 매수/매도 근거
- 오류 발생

매수 알림에는 `매수 근거`, 매도 알림에는 `매도 근거`가 포함됩니다.

## 운영 명령어 요약

```bash
# DB 초기화
PYTHONPATH=src python3 -m gostop.cli init-db

# 전체 수집
PYTHONPATH=src python3 -m gostop.cli collect-all

# 전략 실행
PYTHONPATH=src python3 -m gostop.cli run-strategy

# dry-run 자동매매 1회
PYTHONPATH=src python3 -m gostop.cli autopilot

# 실주문 자동매매 1회
PYTHONPATH=src python3 -m gostop.cli autopilot --live --confirm-live

# 장중 자동 러너
PYTHONPATH=src python3 -m gostop.cli market-runner --live --confirm-live --interval-minutes 5

# 대시보드
PYTHONPATH=src python3 -m gostop.cli dashboard

# 장 마감 리뷰
PYTHONPATH=src python3 -m gostop.cli end-of-day-review
```

## 보안 주의사항

- 실계좌 API 키, 앱 시크릿, 계좌번호, 텔레그램 토큰은 `.env`에만 저장합니다.
- `.env`, DB, 토큰 캐시, 로그는 `.gitignore`로 제외합니다.
- GitHub에는 `.env.example`만 올립니다.
- 실주문 전에는 반드시 대시보드에서 매매 ON/OFF 상태와 주문 후보를 확인합니다.
- 1회 주문 한도와 일일 주문 한도는 반드시 보수적으로 설정합니다.

## 현재 한계와 개선 예정

- 뉴스 감성 분석은 키워드 기반이므로 오탐 가능성이 있습니다.
- 실제 체결 내역 API 연동은 추가 개선 여지가 있습니다.
- Realized P&L은 체결 테이블이 비어 있으면 주문 이벤트 기반 추정치입니다.
- 매도세/호가/체결강도/시장지수 기반 방어 로직은 추가 고도화 대상입니다.
- 백테스트와 실거래 성과 비교 리포트는 별도 모듈로 확장할 수 있습니다.

## 참고 자료

- 한국투자증권 Open API 공식 포털: https://apiportal.koreainvestment.com
- 한국투자증권 Open API 샘플: https://github.com/koreainvestment/open-trading-api
- Jegadeesh and Titman, Returns to Buying Winners and Selling Losers
- Moskowitz, Ooi and Pedersen, Time Series Momentum
- Barroso and Santa-Clara, Momentum has its moments
