# daangn-realty-alert

당근 부동산/중고거래에서 조건에 맞는 신규 매물을 찾아 텔레그램으로 알려주는 개인용 봇.

- 🏠 **부동산**: 서울 전체 신규 매물 중 키워드(무인/카페/양도/권리금 등) 매칭 → 알림
- 🛠 **중고거래**: 서울 전 동네를 회전 순회하며 키워드(무인카페, 커피머신 등) 검색 → 알림

## 동작 구조

부동산은 두 레인으로 신규를 감지한다:

1. **빠른 레인** — 서울 25개 구의 `상가` 카테고리 페이지(SSR 20건씩)를 25분 주기로 폴링. 등록 후 대략 1시간 내 감지.
2. **백스톱 레인** — `sitemap-articles/1`(하루 1회 재생성, 전국 신규 전체)에서 새 ID를 큐에 쌓고, 실행마다 조금씩 `<title>`만 스트리밍으로 읽어 서울 여부를 거른 뒤 상세를 파싱. 빠른 레인이 놓친 매물을 최대 하루 뒤에라도 100% 보정.

중고거래 검색은 동(洞) 단위로만 되므로, 서울 399개 행정동 × 검색어를 (동×키워드) 쌍으로 만들어 실행마다 일정 개수씩 회전 순회한다 (전체 한 바퀴 약 3~4시간).

모든 채널은 `state/seen.json`에 본 매물을 기록해 중복 알림을 막고, GitHub Actions에서는 실행 후 이 파일을 커밋해 상태를 이어간다. 폭주 방지 장치: 부동산은 **첫 성공 실행에서 현재 노출분을 기준점으로만 기록**하고, 등록 후 `freshness_days`(기본 7일)가 지난 매물은 알리지 않는다. 중고거래는 등록 3일 이내 매물만 알린다. 전송에 실패한 알림은 `outbox`에 남아 다음 실행에서 재시도된다.

## 설정

[config.yaml](config.yaml) — 지역, 매칭 규칙(키워드/제외어/매물종류), 검색어, 폴링 주기·예산. 주석 참조.

## 로컬 실행

```bash
pip install -r requirements.txt
python main.py --dry-run     # 텔레그램 없이 콘솔 출력
```

텔레그램 연결:

1. 텔레그램에서 `@BotFather` → `/newbot` → 토큰 발급
2. 만든 봇에게 아무 메시지나 1개 전송
3. `.env` 파일 작성 (`.env.example` 참조) → `python get_chat_id.py`로 chat_id 확인 후 `.env`에 추가
4. `python main.py` — 이후 신규 매물이 텔레그램으로 온다

## GitHub Actions 배포

1. **public 저장소**로 푸시 (private는 무료 Actions 한도 월 2,000분을 1주일 만에 소진함. public은 무제한 무료. 토큰은 secrets라 public이어도 안전)
2. 저장소 Settings → Secrets and variables → Actions에 `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` 등록
3. 끝 — [crawler.yml](.github/workflows/crawler.yml)이 10분 간격(cron 특성상 실제로는 10~30분)으로 돈다. 상태 파일은 봇이 직접 커밋한다.

`seoul_regions.json`(중고거래용 동 id 목록)은 커밋돼 있음. 갱신이 필요하면 `python discover_regions.py`.

## 주의

- 개인 알림 용도로만 쓸 것. 당근 이용약관은 자동화 수집을 금지하므로 수집물 재배포/상업 이용 금지.
- 요청 간격(`poll.*_delay_sec`)을 함부로 줄이지 말 것 — 403/429 차단되면 봇이 해당 실행을 건너뛴다.
- 당근이 HTML 구조를 바꾸면 파서 수정 필요 (`daangn_realty.py`, `daangn_buysell.py`).
