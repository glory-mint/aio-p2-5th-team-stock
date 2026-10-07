# API 명세서

주식 정보 도우미 **살래? 말래?**의 Backend REST API, MCP Client 내부 API, 네 MCP 서버의 Tool 명세입니다. 이 서비스는 종목을 추천하지 않으며 현재가·뉴스·전자공시·커뮤니티 반응을 연결해 사용자가 확인할 정보를 설명합니다.

- 지원 범위: [`shared/supported_companies.json`](../../shared/supported_companies.json)의 2026년 9월 1일 기준 KOSPI 시가총액 상위 20개 보통주 기업
- Backend: `http://BACKEND_HOST:8000` · Swagger: `/docs`
- MCP Client: `http://MCP_CLIENT_HOST:8010`
- MCP 서버: FastMCP Streamable HTTP `/mcp`
- 명세 범위: Backend HTTP 9개, MCP Client HTTP 3개, MCP Tool 8개, MCP Health 4개

계약과 구현이 다르면 실행 코드를 기준으로 작성하고 차이는 [부록 A](#부록-a-계약과-현재-코드의-차이)에 기록했습니다.

---

## 1. 공통 규약

### 1.1 연결 구조와 포트

```text
Frontend --REST/JSON--> Backend --REST/JSON--> MCP Client
                                              ├─ Streamable HTTP --> Price MCP
                                              ├─ Streamable HTTP --> News MCP
                                              ├─ Streamable HTTP --> Disclosure MCP
                                              └─ Streamable HTTP --> Community MCP
```

| 서비스 | 포트 | Health | 주요 경로 |
|---|---:|---|---|
| Backend | 8000 | `GET /health` | `/api/v1/*` |
| MCP Client | 8010 | `GET /health` | `/internal/v1/*` |
| Price MCP | 8020 | `GET /health` | `/mcp` |
| News MCP | 8021 | `GET /health` | `/mcp` |
| Disclosure MCP | 8022 | `GET /health` | `/mcp` |
| Community MCP | 8023 | `GET /health` | `/mcp` |

Frontend는 Backend만 호출합니다. Backend는 사용자 식별자 없이 선택적인 투자 성향 네 값만 MCP Client에 전달하며, MCP 서버에는 투자 성향도 전달하지 않습니다. `/mcp`는 일반 REST 경로가 아니라 MCP 프로토콜 진입점입니다.

### 1.2 데이터 표기

| 항목 | 규칙 |
|---|---|
| 필드명 | `snake_case` |
| 시간 | UTC ISO 8601 문자열, 예: `2026-09-01T09:00:00Z` |
| 종목 코드 | 6자리 숫자 문자열 |
| 금액·비율 | 원 단위 정수·퍼센트 숫자 (`0.72`는 `0.72%`) |
| 값 없음 | 목록은 `[]`, 단일 객체는 `null` |
| 요청·실행 ID | `request_id`, `run_id`는 UUID 문자열로 생성 |

Backend와 MCP Client는 공통 `data` 봉투를 사용하지 않으며 응답 모델의 필드가 JSON 최상위에 위치합니다.

### 1.3 인증

회원 API는 `Authorization: Bearer <access_token>`을 사용합니다. 토큰은 HS256 JWT이며 기본 만료 시간은 24시간이고 `sub`에 `user_id`를 담습니다.

| API | 인증 |
|---|---|
| 로그인·회원가입·기업 목록·Health | 없음 |
| 종목 분석 | 선택. 미지정 시 비회원, 유효한 토큰 지정 시 회원 |
| 투자 성향·Memory | 필요 |

### 1.4 상태와 오류

| `status` | 의미 |
|---|---|
| `success` | 정상 처리 |
| `partial_success` | 선택 자료 일부가 실패했지만 확인된 결과는 유지 |
| `no_data` | 정상 조회했으나 결과가 없음 |
| `unsupported_company` | 미지원 종목 또는 기업명·종목코드 불일치 |
| `invalid_request` | 입력 오류 |
| `unauthorized` | 사용자 또는 외부 제공처 인증 실패 |
| `external_api_error` | 외부 제공처 장애 |
| `timeout` | 호출 시간 초과 |
| `internal_error` | 내부 처리 실패 |
| `error` | Community FGI 어댑터가 원본 오류를 통합한 상태 |

현재 코드는 오류 위치에 따라 두 형식을 사용합니다.

| 경계 | 형식 |
|---|---|
| FastAPI 인증·검증·`HTTPException` | `{"detail": ...}`. 입력 검증은 422 |
| Backend 분석 서비스·MCP Tool 업무 오류 | `status`와 `error.service / code / message / retryable` |

업무 오류 객체의 공통 필드는 다음과 같습니다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `request_id` | string | Backend 분석·일부 MCP 응답에서 오류 추적에 사용 |
| `status` | string | 위 상태값 중 해당 값 |
| `error.service` | string | 실패 경계 또는 MCP 서버 이름 |
| `error.code` | string | 호출부가 분기할 기계 판독 코드 |
| `error.message` | string | 사용자에게 전달 가능한 설명 |
| `error.retryable` | boolean | 같은 요청의 일시적 재시도 가능 여부 |

MCP Tool의 업무 오류는 HTTP 상태가 아니라 Tool 반환 객체의 `status`로 전달합니다. API Key, 내부 Prompt, Stack Trace, DB 주소는 사용자 응답에 포함하지 않습니다.

### 1.5 시간 제한

| 구간 | 계약 기준 |
|---|---:|
| Frontend 분석 요청 | 90초(계약값, 현재 live client는 자체 timeout 미적용) |
| Backend → MCP Client | 75초 |
| MCP Client Workflow | 60초 |
| MCP Tool 1회 | 15초 |
| Agent | 최대 3단계 |

현재 코드의 재시도 구현 여부는 부록 A에 별도로 명시했습니다.

---

## 2. 전체 API 목록

### 2.1 HTTP API

| 서비스 | Method | Path | 인증 | 목적 |
|---|---|---|---|---|
| Backend | GET | `/health` | 없음 | 프로세스 상태 확인 |
| Backend | POST | `/api/v1/auth/login` | 없음 | 로그인·JWT 발급 |
| Backend | POST | `/api/v1/auth/signup` | 없음 | 회원·투자 성향 생성 |
| Backend | GET / PUT | `/api/v1/profile` | 필요 | 투자 성향 조회·수정 |
| Backend | GET / DELETE | `/api/v1/memories/me` | 필요 | 장기·단기 Memory 조회·삭제 |
| Backend | GET | `/api/v1/companies` | 없음 | 지원 기업 20개 조회 |
| Backend | POST | `/api/v1/analyses` | 선택 | 비회원 미리보기·회원 상세 분석 |
| MCP Client | GET | `/health` | 없음(내부용) | 설정 상태 확인 |
| MCP Client | GET | `/internal/v1/mcp-status` | 없음(내부용) | MCP 연결·Tool 목록 확인 |
| MCP Client | POST | `/internal/v1/common-analyses` | 없음(내부용) | 공통 분석 Workflow 실행 |

### 2.2 MCP Tool

| 서버 | Tool | 기본 Workflow | 목적 |
|---|---|---|---|
| Price | `get_stock_quote` | O | 현재가·전일 대비 등락 |
| News | `search_news` | O | 최근 관련 뉴스 |
| Disclosure | `get_recent_disclosures` | O | 최근 공시 목록 |
| Disclosure | `get_disclosure_detail` | 조건부 | Agent가 선택한 공시 원문 앞부분 |
| Disclosure | `search_annual_report` | O | 사업보고서 RAG 검색 |
| Disclosure | `search_periodic_report` | X | 사업·반기·분기보고서 유형별 RAG 검색 |
| Community | `get_community_reaction` | O | 커뮤니티 집계·최신 FGI |
| Community | `get_fear_greed_index` | X | 최신 15분 FGI 단건 |

---

## 3. Backend API

### 3.1 Health — `GET /health`

`200 {"status":"ok"}`를 반환합니다. DB·Redis·MCP 연결까지 검사하지는 않습니다.

### 3.2 Auth — `/api/v1/auth`

#### `POST /api/v1/auth/login`

| 요청 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `username` | string | O | 로그인 사용자명 |
| `password` | string | O | 원문은 응답·DB에 저장하지 않음 |

성공 시 `200`과 다음 응답을 반환합니다.

| 응답 필드 | 타입 | 설명 |
|---|---|---|
| `status` | string | `success` |
| `access_token` / `token_type` | string | JWT / `bearer` |
| `user.user_id` | string | 내부 사용자 ID |
| `user.username` / `user.display_name` | string | 사용자명 / 표시 이름 |
| `profile_completed` | boolean | 투자 성향 존재 여부 |

계정이 없거나 비밀번호가 틀리면 `401`입니다.

#### `POST /api/v1/auth/signup`

| 요청 필드 | 타입 | 필수 | 제약 |
|---|---|---|---|
| `username` / `password` / `display_name` | string | O | 사용자명은 중복 불가 |
| `profile.experience_level` | string | O | `beginner` / `intermediate` / `experienced` |
| `profile.risk_profile` | string | O | `conservative` / `balanced` / `aggressive` |
| `profile.investment_horizon` | string | O | `short` / `medium` / `long` |
| `profile.preferred_evidence` | string | O | `market` / `news` / `financial` / `risk` |

사용자와 성향을 한 트랜잭션으로 생성하고 로그인과 같은 응답을 `200`으로 반환합니다. 비밀번호는 PBKDF2-SHA256 해시로 저장합니다. 사용자명 중복은 `400`, 누락·허용값 위반은 `422`입니다. 현재 요청 모델에는 문자열 길이·비밀번호 복잡도 제약이 없습니다.

### 3.3 Profile — `/api/v1/profile`

| Method | 요청 | 성공 응답 | 주요 오류 |
|---|---|---|---|
| GET | 없음 | 네 투자 성향 필드 | 성향 없음 404, 인증 실패 401 |
| PUT | 네 투자 성향 필드 전체 | 저장된 네 필드 | 인증 단계의 사용자 없음 401, 검증 실패 422 |

PUT은 기존 성향을 갱신하고 없으면 생성합니다. 투자 성향은 설명 난이도와 확인 순서에만 사용하며 매수·매도 적합도를 계산하지 않습니다.

### 3.4 Memory — `/api/v1/memories/me`

| Method | 상태 | 응답·동작 |
|---|---:|---|
| GET | 200 | `user_id`, `long_term`, `short_term` 반환 |
| DELETE | 204 | PostgreSQL 투자 성향과 Redis 단기 Memory를 함께 삭제, 본문 없음 |

`long_term`은 `InvestmentProfile` 또는 `null`, `short_term`은 Redis 상태 또는 `{}`입니다. 회원 분석 성공 시 단기 Memory에 `recent_company_name`, `recent_stock_code`, `searched_at`을 기록합니다. DELETE 후에는 같은 JWT라도 투자 성향이 없으므로 회원 분석의 성향 조회가 `404`가 될 수 있습니다.

### 3.5 Companies — `GET /api/v1/companies`

| 응답 필드 | 타입 | 설명 |
|---|---|---|
| `status` | string | `success` |
| `snapshot_date` | string | `2026-09-01` |
| `companies` | array | 고정 지원 기업 20개 |
| `companies[].rank` | integer | 제외 대상을 뺀 시가총액 순위 |
| `companies[].company_name` / `stock_code` | string | 정식 기업명 / 6자리 코드 |
| `companies[].market` | string | `KOSPI` |

### 3.6 Analyses — `POST /api/v1/analyses`

요청은 `{"query":"삼성전자"}` 한 필드입니다. 양 끝 공백을 제거한 값이 정식 기업명 또는 6자리 종목 코드와 정확히 일치해야 하며 자유 질문·날짜 범위·추천 지시는 받지 않습니다.

#### 공통 성공 응답

| 필드 | 타입 | 설명 |
|---|---|---|
| `request_id` | string | 분석 요청 UUID |
| `status` | string | `success` / `partial_success` |
| `access_level` / `requires_login` | string / boolean | `guest`·true 또는 `member`·false |
| `company` | object | `company_name`, `stock_code`, `supported:true` |
| `price` | object | `current_price`, `change`, `change_rate`, `as_of`, 선택적 `volume_basis`, `volume_as_of` |
| `one_line_summary` | string | 추천 없는 현재 상황 설명 |
| `detail` | object / null | 회원 상세 근거 |
| `personalized_checkpoints` | object / null | 회원 성향별 확인 포인트 |

| 회원 전용 상세 필드 | 설명 |
|---|---|
| `detail.market_temperature` | `score`, `label`, `data_coverage`, `weight_covered` |
| `detail.evidence_level` | `level`(`low / medium / high`), `reason` |
| `detail.news_summary / disclosure_summary / community_summary` | 세 출처 요약 |
| `detail.sources` | 화면 출처 배열 |
| `personalized_checkpoints` | `personal_summary`, `priority_checks`, `caution` |

관심 온도 라벨은 점수 구간으로 확정합니다.

| 점수 | `label` |
|---:|---|
| 0~19 | `관심 낮음` |
| 20~39 | `관심 다소 낮음` |
| 40~59 | `보통` |
| 60~79 | `관심 높음` |
| 80~100 | `관심 매우 높음` |

`detail.sources[]`는 MCP Client가 만든 `source_type`, `title`, 선택적 `url`, 선택적 `published_at`, 출처별 `meta`를 그대로 담습니다. `source_type`은 `price / news / disclosure / community`입니다.

비회원 응답은 가격과 한 줄 설명까지만 제공하며 `detail`, `personalized_checkpoints`가 `null`입니다. 회원 분석은 상세·개인화를 채우고 최근 검색을 Redis에 기록합니다. 회원·비회원 모두 분석 실행 이력을 PostgreSQL에 저장하며 비회원 `user_id`는 `null`입니다.

한 줄 설명과 개인화는 MCP Client 응답을 그대로 사용합니다. Agent(LLM)가 실패하면 MCP Client가 규칙 기반 대체 서사(`services/analysis_builder/narrative.py`)로 채워 보내므로 Backend는 다시 조립하지 않습니다.

#### 미지원 기업과 오류

미지원 query는 MCP Client를 호출하지 않으며 현재 코드는 HTTP `200`으로 다음 필드를 반환합니다.

| 필드 | 값 |
|---|---|
| `status` | `unsupported_company` |
| `message` | KOSPI 시가총액 상위 20개 기업만 지원한다는 안내 |
| `actions` | `지원 기업 20개 보기`, `다른 종목 검색하기` |

| 상황 | HTTP | `status` / `code` |
|---|---:|---|
| MCP Client 시간 초과 | 504 | `timeout` / `MCP_CLIENT_TIMEOUT` |
| 연결 실패·5xx | 500 | `external_api_error` / `MCP_CLIENT_UNAVAILABLE` |
| request ID 불일치 등 신뢰 불가 응답 | 500 | `internal_error` / `MCP_CLIENT_INVALID_RESPONSE` |
| 요청 검증 실패 | 422 | FastAPI `detail` |

---

## 4. MCP Client API

`/internal/v1`은 서비스 역할상 내부 경로이지만 현재 라우터에는 별도 인증 의존성이 없습니다. 배포 시 접근 경계는 네트워크 구성으로 제한해야 합니다.

### 4.1 `GET /health`

| 필드 | 설명 |
|---|---|
| `status` / `service` | `ok` / `mcp_client` |
| `llm_provider` | 설정된 서사 공급자 |
| `openai_configured` | OpenAI Key 설정 여부만 표시 |
| `backend_progress_enabled` | Backend 진행 이벤트 URL 설정 여부 |

### 4.2 `GET /internal/v1/mcp-status`

`services.price / news / disclosure / community`별 `status`와 실제 Tool 이름 배열을 반환합니다. 모두 연결되면 최상위 `connected`, 하나라도 실패하면 `partial`이며 실패 서비스는 `unavailable`, `tools:[]`입니다.

### 4.3 `POST /internal/v1/common-analyses`

#### 요청

| 필드 | 타입 | 필수 | 제약 |
|---|---|---|---|
| `request_id` | string | O | 1~100자 |
| `company.company_name` | string | O | 1~100자 |
| `company.stock_code` | string | O | 6자리 숫자 |
| `investment_profile` | object / null | O | 네 허용 성향 값 또는 `null` |
| `requested_at` | string | O | 현재 모델은 시간 문자열을 별도 파싱하지 않음 |

정의되지 않은 추가 필드는 거부합니다.

#### 응답

| 필드 | 설명 |
|---|---|
| `request_id / run_id` | 요청 ID / Workflow UUID |
| `status / termination_reason` | `success`·`partial_success` / 종료 이유 |
| `company / price` | 정식 기업 식별 정보 / 현재가 정보와 `source_name` |
| `common_analysis` | 한 줄, 관심 온도, 근거 수준, 세 출처 요약 |
| `personalized_checkpoints` | 성향이 있으면 값, 비회원은 `null` |
| `sources / partial_failures` | 화면 출처 / 제외된 서비스의 상태·메시지 |
| `collected_at` | 취합 시각 |
| `trace_summary` | Tool·LLM 호출 수, 성공·실패 Tool, `duration_ms` |

`common_analysis.market_temperature`에는 `score`, `label`, `data_coverage`, 가용 입력의 최대 배점 합인 `weight_covered`, 실제 배점 구성인 `components`가 포함됩니다. `weight_covered`는 0~100이며 구버전 응답을 재검증하는 Backend에서는 필드가 없을 때 100을 기본값으로 사용합니다.

| 중첩 객체 | 필드 |
|---|---|
| `price` | `current_price`, `change`, `change_rate`, `as_of`, 선택적 `source_name`, `volume_basis`, `volume_as_of` |
| `common_analysis` | `one_line_summary`, `market_temperature`, `evidence_level`, `news_summary`, `disclosure_summary`, `community_summary` |
| `market_temperature` | `score`(0~100), `label`, `data_coverage`, `weight_covered`(0~100), `components` |
| `evidence_level` | `level`(`low / medium / high`), `reason`, 연결 공시 `matched[]`, 미연결 이슈 `unmatched[]`, 최근 30일 주요 공시 수 `material_count` |
| `personalized_checkpoints` | `personal_summary`, 1~3개의 `priority_checks`, `caution` |
| `sources[]` | `source_type`, `title`, 선택적 `url / published_at`, `meta` |
| `partial_failures[]` | `service`, `status`, `message` |
| `trace_summary` | `tool_calls`, `llm_calls`, `completed_tools`, `failed_tools`, `duration_ms` |

| `components` | 입력 | 0~1 정규화 | 최대 배점 |
|---|---|---|---:|
| `volume_activity` | 20일 평균 대비 거래량 비율. 없으면 `1 + 전일 대비 거래량 변화율 / 100`으로 대체 | `clamp(ratio / 3, 0, 1)` | 30 |
| `news_attention` | 관련 기사 수 + 가장 오래된 관련 기사부터 지금까지 시간(`span_hours`) | 100건이 쌓이는 시간 6시간 이하 1, 7일 이상 0, 로그 스케일. `span_hours` 없으면 `clamp(count / 80, 0, 1)` | 25 |
| `community_activity` | 지난 7일 글 수의 이전 28일 주간 평균 대비 비율 | `clamp(ratio / 3, 0, 1)` | 25 |
| `fear_greed_intensity` | 공포탐욕 지수 | `abs(fgi - 50) / 50` | 20 |

각 항목은 출처가 성공하고 필요 입력이 있을 때만 `components`에 담습니다. 미가용 항목은 빼고 `score = round(가용 항목 점수 합 / weight_covered × 100)`으로 재정규화하며, 가용 배점이 0이면 0점입니다. 공시 건수와 주가 등락률은 시장 관심 온도 산식에 사용하지 않습니다.

`evidence_level`은 자료 종류 수가 아니라 현재 이슈와 최근 30일 주요 비정기 공시의 연결 여부로 계산합니다. 커뮤니티 기대 3개·우려 2개를 우선하고 제목에 정식 회사명이 포함된 뉴스 2개를 보조 이슈로 사용합니다. 규칙 사전으로 연결된 공시가 있으면 `high`, 연결은 없지만 주요 공시가 있으면 `medium`, 주요 공시가 없으면 `low`이며, 공시 조회 실패도 사유를 밝힌 `low`입니다. 임베딩·유사도 하한은 사용하지 않습니다. `sources[].meta`의 `confirmed`/`unconfirmed`는 연결·미연결 이슈를, `disclosure_kind`는 `major`/`periodic`/`other`를 뜻합니다.

현재 성공 응답에서 생성되는 종료 이유는 `completed`, `partial_completed`, `model_error`, `invalid_tool_call`, `max_steps_exceeded`입니다. Workflow 시간 초과는 응답 모델이 아니라 HTTP 504로 종료합니다.

#### 처리와 오류

1. Price·News·정기공시·최근 30일 주요 비정기 공시·사업보고서·Community의 여섯 기본 Tool을 병렬 호출합니다.
2. Price가 성공하지 않으면 필수 자료 누락으로 `503`을 반환합니다.
3. 선택 자료 실패는 성공 자료를 유지하고 `partial_success`로 표시합니다.
4. 관심 온도·근거 수준을 규칙으로 계산하고 Agent가 설명합니다.
5. Agent는 최근 목록의 접수번호로 공시 상세를 조건부 조회합니다.

| 상황 | HTTP | 응답 |
|---|---:|---|
| 요청 검증 | 422 | FastAPI `detail` |
| Workflow 시간 초과 | 504 | `분석 시간이 초과되었습니다.` |
| 필수 Price 실패 | 503 | `현재 가격을 확인하지 못했습니다.` |
| 그 밖의 예외 | 500 | `분석 중 내부 오류가 발생했습니다.` |

---

## 5. MCP 서버와 Tool

### 5.1 Price MCP (`:8020/mcp`)

Health는 `status:ok`, `service:price_mcp`, KIS 자격 증명 여부 `configured`를 반환합니다.

#### `get_stock_quote`

| 구분 | 필드·규칙 |
|---|---|
| 입력 | `company_name`은 공백 제거 후 필수, `stock_code`는 공백 제거 후 6자리 숫자 |
| 성공 | 기존 가격 필드와 `volume`, `volume_change_rate`, `avg_volume_20d`, `volume_ratio_20d`, `volume_basis`, `volume_as_of`, `projected_volume`, `warnings` |
| 부호 | 상승 양수, 하락 음수, 보합 0 |
| 캐시 | 종목별 성공 응답, 계약 기본 TTL 60초 |
| 결과 없음 | `no_data`와 기업명·코드·제공처·수집시각 |

거래량 기준은 Asia/Seoul 시각과 일봉 `output2`의 오늘 행 존재 여부로 고른다. 거래일 장중(09:00~15:30 전)은 `intraday_pace`로 누적 거래량을 장 마감 페이스로 환산하고, 장 마감 뒤에는 `today_close`, 비거래일이나 장 시작 전에는 `last_session`을 사용한다. `volume_as_of`는 기준 거래일이며 `projected_volume`은 `intraday_pace`에서만 값이 있다. 일봉 조회 실패 시 세 필드는 모두 `null`이다.

| 상황 | `status` / `error.code` | 재시도 |
|---|---|---|
| 기업명·코드 오류 | `invalid_request` / `INVALID_COMPANY_NAME` 또는 `INVALID_STOCK_CODE` | X |
| 자격 증명 없음·인증 실패 | `unauthorized` / `KIS_CREDENTIALS_MISSING` 또는 `KIS_API_UNAUTHORIZED` | X |
| 시간 초과·장애 | `timeout` / `KIS_API_TIMEOUT`, `external_api_error` / `KIS_API_UNAVAILABLE` | O |

### 5.2 News MCP (`:8021/mcp`)

Health는 `status:ok`, `service:news_mcp`, `mock` 여부를 반환합니다.

#### `search_news`

| 입력 | 기본값·제약 |
|---|---|
| `company_name`, `stock_code` | 필수, 코드는 6자리 숫자 |
| `lookback_days` | 선택, 설정 기본 7, 1~30 |
| `limit` | 선택, 설정 기본 10, 1~10 |

| 출력 | 설명 |
|---|---|
| `request_id`, `status`, `company_name`, `stock_code` | 식별·상태 |
| `articles`, `result_count`, `collected_at` | 중복·무관·기간 밖 기사를 제거한 결과 |
| `articles[]` | `headline`, `publisher`, `published_at`, `summary`, `source_url`, `relevance` |
| `mock` | Mock 모드 응답에 포함되는 boolean |

기사가 없으면 `no_data`와 빈 배열을 반환합니다. 본문 전체를 크롤링하지 않으며 MCP Client는 최대 5건만 사용합니다. 오류 코드는 입력 `INVALID_REQUEST`, 인증 `NEWS_API_UNAUTHORIZED`, 시간 초과 `NEWS_API_TIMEOUT`, 장애 `NEWS_API_UNAVAILABLE`입니다.

### 5.3 Disclosure MCP (`:8022/mcp`)

Health는 `status:ok`, `service:disclosure_mcp`를 반환합니다. 성공 응답에는 `request_id`, `source_type:"dart"`가 추가되고 종목 기반 Tool은 입력 `stock_code`와 제공된 `company_name`도 추가합니다.

#### `get_recent_disclosures`

| 입력 | 기본값·제약 |
|---|---|
| `stock_code` | 필수, 6자리 지원 종목 |
| `company_name` | 선택, 제공 시 DB 정식명과 일치 |
| `lookback_days` / `limit` | 30 / 20, 허용 1~365 / 1~100 |

출력은 `status`, `disclosures[]`, `collected_at`입니다. 각 공시는 `report_name`, `receipt_number`, `published_at`, `document_type:"disclosure"`, `source_url`을 가집니다. 최신순으로 반환하고 메타데이터를 DB에 upsert하며, 결과 없음은 `no_data`와 빈 배열입니다.

#### `get_disclosure_detail`

입력은 `receipt_number`입니다. 출력은 `status`, 선택적 `report_name / published_at`, `receipt_number`, `document_type`, `content`, `content_truncated`, `total_chars`, `summary`, `source_url`, `collected_at`입니다. 문단·표를 평탄화한 원문의 앞 3,000자만 반환합니다.

#### `search_annual_report` · `search_periodic_report`

| 입력 | annual | periodic |
|---|---|---|
| `stock_code`, `query` | 필수 | 필수 |
| `report_type` | `annual` 고정 | `annual / semi_annual / quarterly` 필수 |
| `company_name` | 선택, 정식명 일치 | 동일 |
| `top_k` | 기본 5, 1~5 | 동일 |
| `report_year` | 선택, 기본 직전 연도 | 선택, 유형별 최근 보고 연도 |

두 Tool은 `status`, `report_name`, `receipt_number`, `report_year`, `report_type`, `matched_passages`, `available_years`, `source_url`, `collected_at`을 반환합니다. 각 passage는 `section`, `text`, 소수점 6자리 `score`, `match_type:"vector"`입니다. 대상 보고서가 없으면 수집·청킹·임베딩 후 검색하며 관련 청크만 반환합니다.

| Disclosure 오류 | `status` / `error.code` | 재시도 |
|---|---|---|
| 미지원·기업 불일치 | `unsupported_company` / `UNSUPPORTED_COMPANY` | X |
| 입력·설정·파싱 | `invalid_request` / `INVALID_REQUEST` | X |
| 보고서 없음 | `no_data` / `NO_DATA` | X |
| DART 시간 초과 | `timeout` / `DART_TIMEOUT` | O |
| DART·Embedding 오류 | `external_api_error` / `EXTERNAL_API_ERROR` | O |
| 내부 오류 | `internal_error` / `INTERNAL_ERROR` | X |

### 5.4 Community MCP (`:8023/mcp`)

Health는 `status:ok`, `service:community_mcp`, `mock` 여부를 반환합니다.

#### `get_community_reaction`

| 입력 | 기본값·제약 |
|---|---|
| `company_name`, `stock_code` | 필수, 코드는 6자리 숫자 |
| `lookback_days` | 선택, 설정 기본 7, 1~28 |
| `limit` | 선택, 설정 기본 100, 1~500 |

| 출력 | 설명 |
|---|---|
| `status`, `company_name`, `stock_code` | 원본 상태와 종목 |
| `sample_status`, `period`, `sample_size`, `sentiment` | 집계 범위·표본·감성 건수 |
| `top_topics`, `representative_evidence` | 기대·우려 주제와 비식별 짧은 근거 |
| `fgi_mean`, `fgi_latest` | 원본에 있을 때 기간 평균·최신 FGI. 최신값이 없으면 `null` |
| `source_name`, `source_detail`, `collected_at` | 제공처·수집시각 |
| `note`, `supported_codes` | 원본에 있을 때만 포함 |
| `mock` | Mock 모드 응답에 포함되는 boolean |

`period`는 `from / to`, `sentiment`는 `positive_count / neutral_count / negative_count`, `top_topics`는 `expectations / concerns`를 사용합니다. 대표 근거에는 `text / posted_at`과 원본에 있는 `sentiment`가 전달되며, 최신 FGI에는 `fgi / label / as_of / post_count / valence_percentile`이 올 수 있습니다.

원문 게시글 전체는 MCP Client와 LLM에 전달하지 않습니다. 입력 오류는 `INVALID_REQUEST`, 인증·시간 초과·장애는 각각 `COMMUNITY_API_UNAUTHORIZED`, `COMMUNITY_API_TIMEOUT`, `COMMUNITY_API_UNAVAILABLE`입니다.

#### `get_fear_greed_index`

입력은 `company_name`, 6자리 `stock_code`입니다.

| 출력 | 설명 |
|---|---|
| `request_id`, `status`, `company_name`, `stock_code` | `status`는 `success / no_data / unsupported_company / error`; 잘못된 코드는 `invalid_request` |
| `fgi`, `label`, `as_of`, `post_count`, `summary` | 최신 15분 FGI와 원본 설명 |
| `warnings`, `source_name`, `source_detail`, `collected_at` | 경고·제공처·수집시각 |
| `error` | `status:error` 또는 잘못된 입력일 때 오류 상세 |
| `mock` | Mock 모드 응답에 포함되는 boolean |

원본 `status:"empty"`는 사유에 `지원`이 있으면 `unsupported_company`, 그 외에는 `no_data`로 변환합니다. 원본 인증·장애·시간 초과는 `status:"error"`와 `error` 객체로 통합합니다.

---

## 6. 설계 의도

| 원칙 | 설계 의도 |
|---|---|
| 개인화 격리 | Backend만 사용자를 알고 MCP Client에는 성향만, MCP 서버에는 성향도 전달하지 않습니다. |
| 공개 범위 제한 | 비회원은 가격·한 줄만 받고 상세 근거와 개인화는 `null`입니다. |
| 필수·선택 자료 분리 | Price 실패는 중단하고 나머지 일부 실패는 `partial_success`로 확인된 결과를 유지합니다. |
| 점수와 설명 분리 | 관심 온도·근거 수준은 규칙으로 계산하고 Agent는 제한된 근거를 설명합니다. |
| 최소 원문 | 뉴스 최대 5건, 화면 공시 최대 2건, 보고서 청크 최대 5개, 커뮤니티 집계만 사용합니다. |
| 추천 방지 | 성향은 설명 순서에만 사용하며 요청 모델에도 매수·매도 추천용 자유 질문 필드가 없습니다. |

---

## 7. 검증 체크리스트

- [x] Backend `main.py`, 라우터 4개와 요청·응답 schemas를 대조했습니다.
- [x] MCP Client HTTP 3개와 Workflow·응답 모델을 대조했습니다.
- [x] MCP 서버 4개의 Health와 등록 Tool 8개를 실제 코드에서 확인했습니다.
- [x] Tool 기본값·허용 범위·성공·결과 없음·오류 필드를 schemas와 services에서 교차 확인했습니다.
- [x] 지원 기업 기준 파일과 문서 상대 링크가 실제 트리에 존재함을 확인했습니다.
- [x] 계약 차이는 코드 우선으로 부록에 분리하고 추측 필드는 넣지 않았습니다.

---

## 부록 A. 계약과 현재 코드의 차이

| 구분 | 계약·설명 | 현재 코드 기준 | 영향 |
|---|---|---|---|
| Backend Memory | Frontend ↔ Backend Endpoint 표에 없음 | GET·DELETE `/api/v1/memories/me` 구현 | 실제 API에 포함 |
| 미지원 기업 HTTP | 공통 오류 계약은 400 | 분석 라우터는 응답 객체를 그대로 반환하여 200 | 본문의 `status` 확인 필요 |
| 오류 봉투 | 공통 업무 오류 객체를 정의 | 일부는 FastAPI `detail`, 분석 서비스·Tool은 `status/error` | 경계별 파싱 필요 |
| 재시도 | 네트워크·5xx 1회 재시도 | Backend·MCP 호출 코드에 재시도 루프 없음 | 현재 호출당 1회 |
| Frontend 시간 제한 | 분석 요청 90초 | live client는 `fetch`에 AbortSignal·timeout을 설정하지 않음 | 브라우저 요청 제한 구현 필요 |
| MCP Client 운영 API | 분석 계약은 분석 POST 중심 | Health와 `mcp-status`도 구현 | 운영 경로 추가 |
| 관심 온도 | Backend는 `score`, `label`, `data_coverage`, `weight_covered`를 응답 | MCP Client는 `components`도 직렬화 | Backend는 화면 필요 필드를 재검증해 전달 |
| 종료 이유 | 계약 목록에는 `mcp_tool_error`, `workflow_timeout`도 있음 | 성공 응답은 `completed / partial_completed / model_error / invalid_tool_call / max_steps_exceeded`; Workflow timeout은 HTTP 504 | 응답 JSON과 HTTP 오류를 분리 처리 |
| Disclosure Tool | 계약·함수 docstring은 3개 | `search_periodic_report` 포함 4개 등록 | 네 번째 Tool 명시 |
| 최근 공시 범위 | 기본 30일·20건 | 허용 범위 1~365일·1~100건 | 코드 범위 반영 |
| 보고서 검색 | 핵심 입력·출력만 예시 | `report_year`, `report_type`, `available_years`, `match_type` 추가 | 확장 필드 반영 |
| 공시 상세 | 제목·핵심 내용·URL 설명 | 원문 3,000자와 잘림·전체 길이 메타 반환 | 실제 구조 반영 |
| Community 표본 | 표본 수별 상태 규칙 | 어댑터는 원본 `/reaction` 상태를 로컬 재계산 없이 전달 | 원본 계약 준수 필요 |
| Community FGI | 공통 상태에 `error` 없음 | 원본 오류를 `status:error`로 통합 | FGI 호출부 별도 처리 |
| Frontend 분석 상태 | live 타입·화면이 `partial_completed`를 기대했음 | PR #33에서 `partial_success`로 통일. `partial_completed`는 MCP Client의 `termination_reason`에만 남음 | 정합화 완료 |
| Frontend Profile | live 타입은 `{status, profile}` 봉투를 기대 | Backend GET·PUT은 네 성향 필드의 raw 객체를 반환하고 live client가 봉투로 감쌈(PR #33) | 정합화 완료 |

## 부록 B. 구현 근거

| 영역 | 파일 |
|---|---|
| 공통 계약 | [`docs/specs/CONNECTION_CONTRACT.md`](CONNECTION_CONTRACT.md), [`docs/specs/contracts/errors.md`](contracts/errors.md) |
| Backend | [`docs/specs/contracts/frontend_backend.md`](contracts/frontend_backend.md), `backend/app/main.py`, `backend/app/routers/*/router.py`, `backend/app/schemas/*.py` |
| MCP Client | [`docs/specs/contracts/analysis.md`](contracts/analysis.md), `mcp_client/app/api/routes.py`, `mcp_client/app/schemas/analysis.py` |
| MCP Tool | `mcp_servers/price_mcp/app/schemas/`, `mcp_servers/news_mcp/app/schemas/`, [`disclosure`](contracts/disclosure.md), [`community`](contracts/community.md), 각 `mcp_servers/*/app/tools/*.py` |
| 투자 성향 | `backend/app/schemas/`, `backend/app/schemas/profile.py` |
