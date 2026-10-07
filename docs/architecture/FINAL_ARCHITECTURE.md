# 최종 아키텍처

이 문서는 2026-09-07 현재 코드와 연결 계약을 기준으로 서비스 책임을 정리합니다. Agent의 상세 상태·성찰은 [에이전트 아키텍처 설계서](agent-architecture.md), 실제 측정은 [시험 결과 보고서](../reports/agent-test-report.md)를 참조합니다. 성찰 브랜치 구현과 live 환경 배포 상태는 구분합니다.

## 1. 서비스 목적

사용자가 지원 종목을 검색하면 가격·뉴스·기업보고서·커뮤니티 데이터를 모아 현재 상황과 그 근거를 설명합니다. 서비스는 투자 결정을 대신하지 않습니다.

## 2. 전체 흐름

```text
사용자
  → Frontend
  → Backend
      ├─ 지원 기업 확인
      ├─ 비회원/회원 확인
      └─ 회원의 투자 성향·Memory 조회
  → MCP Client
      ├─ 결정적 Workflow
      ├─ Stock Analysis Agent 1개
      └─ 네 MCP 결과 취합·공통 분석
  → Price MCP ─────── 실시간 가격 API
  → News MCP ──────── 최신 뉴스 API
  → Disclosure MCP ── DART + PostgreSQL/pgvector
  → Community MCP ─── 커뮤니티 데이터 서버
```

표준 분석은 Workflow가 기본 Tool 6개를 병렬 호출합니다. `get_stock_quote`, `search_news`, `get_recent_disclosures`, `get_material_disclosures`, `search_annual_report`, `get_community_reaction`이며, 주요 공시 작업은 실제 MCP의 `get_recent_disclosures`에 30일·유형 필터를 전달합니다. Agent는 `get_disclosure_detail`만 선택하며 모델 호출 예산은 최초 1회 + 후속 최대 3회입니다. 성찰 on은 상세 최대 2건과 성찰 추가 호출 최대 2회를 Runtime에서 강제합니다.

## 3. 서비스 책임

### Frontend

- Backend만 호출한다.
- 검색·공개 결과·로그인·상세 근거·개인화 결과를 표시한다.
- API Key, DB 접속정보, 투자 성향 원본을 관리하지 않습니다.

### Backend

- 지원 기업 20개를 확인한다.
- JWT 로그인·회원가입과 데모 계정 10개를 제공합니다.
- 투자 성향과 Memory를 조회한다.
- MCP Client에 선택적으로 성향 네 값을 보내고 비회원/회원 응답을 조립합니다.
- 한 줄 결론·개인화 서사는 MCP Client 응답을 그대로 씁니다. Agent(LLM)가 실패하면 MCP Client가 규칙 기반 대체 서사를 채워 보냅니다.

### MCP Client

- Backend가 호출하는 통합 서버입니다.
- Price·News·Disclosure·Community MCP를 발견·호출한다.
- 네 서버의 여섯 기본 조회 결과를 정규화하고 규칙 기반 관심 온도·근거 수준과 Agent 설명을 만듭니다.
- 사용자 ID·로그인 토큰·장기 Memory 원문은 받지 않습니다. `investment_profile`의 경험·위험 성향·투자 기간·선호 근거 네 값은 받아 `personalized_checkpoints`를 생성합니다. 저장·수정·삭제 책임은 Backend에 있습니다.
- 외부 원본 데이터 API를 직접 호출하거나 원본을 저장하지 않습니다.

### 네 MCP 서버

- 데이터 수집, 검증, 정제와 출처 유지만 담당한다.
- 소스별 짧은 요약은 가능하지만 최종 시장 온도나 사용자 적합도를 판단하지 않습니다.
- 서로 직접 호출하지 않습니다.
- 사용자 정보를 받지 않습니다.

## 4. MCP 공통 구조

Community MCP를 기준 구현으로 사용한다.

```text
각_mcp/
├─ server.py              # FastMCP 생성, Tool 등록, /health
├─ app/
│  ├─ tools/              # 외부에 공개하는 MCP Tool
│  ├─ services/           # 데이터 정제·계산·업무 규칙
│  ├─ clients/            # 외부 API·DB 연결
│  ├─ schemas/            # 입력·출력 형식
│  ├─ core/               # 설정·로그
│  └─ rag/                # Disclosure MCP에만 사용
├─ tests/
├─ .env.example
├─ requirements.txt
├─ Dockerfile             # 실행 코드 완성 후 추가
```

`server.py`에는 데이터 처리 로직을 넣지 않습니다.

## 5. LLM 사용 위치

### 공통 분석

MCP Client가 제한된 자료와 Python이 계산한 관심 온도·근거 수준을 OpenAI `gpt-5.6-luna`에 전달합니다. 모델 출력은 `Narrative`의 strict JSON Schema로 제한합니다.

- 공통 한 줄 설명
- 뉴스·보고서·커뮤니티 요약
- 성향이 있으면 개인화 확인 포인트, 없으면 null

온도·라벨·공식 근거 수준·출처·실패 목록은 Workflow가 조립하며 모델이 생성하거나 변경하지 않습니다. 근거 수준은 최근 30일 주요 공시와 현재 이슈의 키워드 매칭 기준입니다.

### 개인화

MCP Client Agent가 회원의 네 가지 성향 값과 수집 근거로 다음을 생성하며, Backend가 서술 채택 정책에 따라 전달하거나 규칙 기반으로 대체합니다.

- 개인화 한 줄 설명
- 먼저 확인할 항목 1~3개(Agent Schema 기준)
- 주의할 점 1개

온도·근거 수준의 규칙 계산은 성향과 무관합니다. 다만 성향 값이 같은 모델 요청에 함께 들어가므로, 공통 설명 문장까지 사용자마다 글자 단위로 같다고 보장하지는 않습니다.

## 6. 저장소 책임

| 데이터 | 책임 위치 |
|---|---|
| 사용자·투자 성향·장기 Memory | Backend + PostgreSQL |
| 세션·짧은 캐시 | Backend + Redis |
| 기업보고서 원문 메타데이터·임베딩 | Disclosure MCP + PostgreSQL/pgvector |
| 실시간 가격·뉴스 | 원본 API 조회, 필요 시 각 MCP의 짧은 캐시 |
| 커뮤니티 집계 | 외부 커뮤니티 서버, Community MCP는 조회·변환 |
| 지원 기업 Snapshot | `shared/supported_companies.json` |

MVP에서는 MCP마다 별도 PostgreSQL 인스턴스를 만들지 않는다. 하나의 PostgreSQL/pgvector 인스턴스를 사용하되 테이블 책임을 구분한다.

## 7. 화면 접근 수준

```text
비회원 검색
  → 기업명·가격·등락·공통 한 줄 설명
  → 상세 버튼 클릭 시 회원가입 안내

회원 검색
  → 비회원 결과
  → 시장 온도·근거 요약·출처
  → 투자 성향에 맞춘 확인 포인트
```

## 8. 이번 프로젝트에서 하지 않는 것

- 자동 매수·매도 추천
- 목표주가와 수익률 예측
- 상위 20개 밖의 종목 분석
- 종목별 사용자 커뮤니티
- Multi-Agent 구조
- 실제 증권 주문
- 포트폴리오·매매내역 관리

관리자용 실황 페이지는 Backend에 구현되어 있으며 최근 검색·분석 이력과 부분실패 집계를 표시합니다. Agent 내부 진행 이벤트 전문을 저장하는 기능은 아닙니다.

## 9. 변경 규칙

입출력 필드나 Tool 이름을 바꿀 때는 `docs/specs/contracts/`를 먼저 수정한다. 서비스 구현과 문서는 계약 변경 후 함께 갱신한다.
