# API-14 로그인 후 자동 정책 추천

POST  /api/v1/chat/recommendations  ·  v1.0 로컬 구현 기준 (2026-09-18)

### 계약 정보

| 항목 | 정의 |
| --- | --- |
| 상태 / 버전 | v1.0 · 2026-09-18. API-14·자동 모드·노드 총 90초·D11 로깅이 반영된 로컬 코드 기준 정의서. 독립 인수·푸시·배포·실연동 완료를 의미하지 않는다. |
| 개정 이력 | v0.1 승인 계약 → v0.2 공용 D5/API-11 반영 → v1.0 실제 로컬 구현과 대조. 경로·입력·업무 규칙·오류 코드는 기존 계약 유지. v0.1/v0.2 및 Downloads 원본은 보존. |
| 구현 기준 / 진행 상태 | 고정 커밋 532a5548db28275959891e2f25f0459464634704. API-14 구현 커밋 6b40aeedb8498db52ef54ee617ac13d997933afc. D2/D3/D4/D5/D8/D11 포함. 계약 결정·구현·검증 범위는 [백엔드 계약 추적표](../backend/README.md#원본-문서와-남은-계약-차이)에서 관리한다. 저장소 반영과 배포는 별개다. |
| API ID / 목적 | API-14. 성공한 로그인 다음에 저장된 DB 프로필로 자동 추천. 원본 INDEX의 API-01~13 뒤에 추가한 별도 경로이며 로그인 응답은 그대로다. |
| 관련 화면 / Gate | S-01 로그인 다음 S-03·S-06. 정책 상세 S-07·비교 S-10 재사용. Gate 5. |
| 선택 근거 | POST /api/v1/chat/recommendations: 새 그래프 실행·채팅 세션을 만들므로 POST. v0.1에서 승인한 기존 chat 네임스페이스 및 ChatResponse 재사용 계약을 유지한다. |
| 결정 출처 | 업무 규칙은 최종 사용자 승인과 [백엔드 계약 추적표](../backend/README.md#원본-문서와-남은-계약-차이). 경로·API 번호·본문 검증·null 표현·오류 매핑은 v0.1에서 제안 후 승인된 엔지니어링 계약이다. 과거 PM 발언으로 인용하지 않는다. |
| 기존 로그인 | API-02 요청·응답·쿠키 발급을 변경하지 않는다. 추천 실패는 이미 성공한 로그인 실패가 아니다. |
| 관련 구현 위치 | [chat.py::recommend](../backend/app/api/v1/chat.py) → [deps.py::user_operation](../backend/app/api/deps.py) → [chat_adapter.py::start_recommendations](../backend/app/services/chat_adapter.py) → [service.py::ask](../src/rag_chatbot/service.py). 공용 ChatResponse 재사용. |

### Request — 인증 및 입력

| 필드명 | 타입 | 필수 | 설명 / 허용값·제약조건 |
| --- | --- | --- | --- |
| Cookie: session_id | string | 필수 | API-02가 설정한 기존 HttpOnly 로그인 세션 쿠키. 불투명 토큰을 서버에서 검증. JWT/Bearer/새 사용자 헤더를 추가하지 않는다. |
| Path / Query | 없음 | 해당 없음 | 세션 ID·사용자 ID를 경로/쿼리에 받지 않는다. 쿼리 전달은 400으로 거절하는 v0.1 승인 계약. |
| Request Body | 없음 | 해당 없음 | 본문을 보내지 않는다. JSON {}를 포함한 본문 전달은 400으로 거절하는 v0.1 승인 계약. client user_id/profile/known_*/message/top_k/session_id를 받지 않는다. |

### Response Headers

성공·오류 응답은 Content-Type: application/json 및 Cache-Control: no-store. 기존 인증 쿠키만 사용한다. 추천 전용 쿠키·캐시·멱등 키·ETag·추가 헤더 체계는 만들지 않는다. no-store는 브라우저 저장 방지이고 기존 서버 세션 수명과 별개다.

### Response — 성공 (200 OK)

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| status | string | 항상 answered. 정상 자동 추천은 needs_input을 반환하지 않는다. |
| session_id | string | 서버가 새로 발급한 UUID 채팅 세션 ID. 로그인 쿠키와 별개. 빈 결과도 발급하며 API-11/12/13에 사용한다. |
| question | string\|null | 항상 null. 초기 프로필 수집·충돌 확인·금액 계산 질문을 하지 않는다. |
| missing_slots | string[] | 항상 []. 값이 없다는 사실을 채웠다는 뜻이 아니다. 내부 미상 상태는 유지한다. |
| interrupt_id | string\|null | D5 공용 필드. API-14는 질문이 없어 항상 null. API-11 계산 응답에는 현재 LangGraph interrupt ID를 그대로 반환한다. 인증·채팅 세션 ID와 별개다. |
| calc_missing_slots | string[] | D5 공용 필드. API-14는 항상 []. 일반 상담에서는 금액 계산에 필요한 미입력 슬롯 이름. |
| calc_missing_choices | CalculationChoice[] | D5 공용 필드. API-14는 항상 []. 일반 상담은 policy_id/labels/policy_title로 현재 정책의 정확한 선택지를 제공한다. |
| calc_slot_inputs | CalculationSlotInput[] | D5 공용 필드. API-14는 항상 []. 일반 상담은 select/number 위젯, 실제 코드·한글 라벨·숫자 경계를 제공한다. |
| slot_conflicts | object\|null | 항상 null. 자동 모드에서 DB 저장값을 임의의 파서 추론으로 덮거나 되묻지 않는다. |
| answer_status | string\|null | 기존 complete/partial/abstained 판정 보존. abstained면 개별 충족 여부와 무관하게 policies=[]이다. null은 미제공이며 이를 complete로 지어내지 않는다. |
| final_answer | string\|null | 카드가 있으면 최종 카드·금액과 일치하는 안내. 카드 0건이면 정확히 현재 정보로 추천할 정책이 없습니다 (마침표 없음). |
| final_citations | object[] | 최종 안내와 남긴 카드의 근거. 제외된 카드만 뒷받침하는 인용 제거. 0건은 []. 항목은 기존 사전 구조를 유지한다. |
| policies | PolicyView[] | 최종 추천 카드. 자격 충족만 반환. 미확인·미충족·기타 미판정 제외. 아래 계약참조의 전체 필드 정의 참조. |
| output_json | object | 현재 구조 재사용: status/session_id/answer_status/final_answer/summary/profile/evidence_count/final_citations/policies. 최상위 결과와 동기화. |
| output_text | string\|null | 남긴 카드·계산 가능한 금액으로 다시 구성. 0건은 final_answer와 같은 정확한 문구. 미산정 금액을 0원·확인 필요 금액으로 표시하지 않는다. |
| output_markdown | string\|null | 같은 결과의 비교 표현. 미산정 지원금 셀은 비워 두고 허위 숫자나 이전 계산값을 남기지 않는다. 0건은 동일 문구. |
| llm_status | object | 기존 진단 객체 유지. enabled/model/calls/successes/failures/messages. 새 승자 provider 필드 없음. API-12 정책 변경 아님. |
| timing | object | 기존 단계별 시간·노드 경로 진단 객체 유지. 새로운 요청 전체 90초 보장을 의미하지 않는다. |

### 필드 재사용과 직렬화

API-14 전용 새 응답 필드 0개. 고정 커밋 532a5548의 공용 ChatResponse 18개·PolicyView 27개·PolicyDetail 20개는 v0.2 공용 D5 스키마와 같다. 이 65개 필드와 공용 CalculationSlotInput 6개·CalculationChoice 3개를 정의한다. 200 응답은 모델의 null/[]/{} 기본값까지 포함해 직렬화한다. 기존 extra 허용은 유지한다.

공용 확장 필드는 interrupt_id, calc_missing_slots, calc_missing_choices, calc_slot_inputs이다. 자동 추천 응답에서는 각각 null, [], [], []이며 질문을 만들지 않는다. 일반 상담의 계산 interrupt 응답과 그 output_json에는 실제 질문 ID·입력 정보·선택지가 담긴다. API-14는 calc_answers 입력을 받지 않는다.

### 자동 추천 처리 규칙

| 규칙 | 로컬 구현 계약 |
| --- | --- |
| 프로필 신뢰 경계 | 인증 토큰에 연결된 실제 account ID로 원격 DB 저장 프로필을 읽는다. 클라이언트 입력·로그인 응답 복사본을 추천 데이터로 받지 않는다. 사용자 잠금 안에서 생존 계정 재확인과 그래프 실행을 연결한다. |
| 누락 / 질문 없음 | 가입 선택값 미입력은 unknown. employment_status는 미수집이므로 미취업으로 간주하지 않는다. 초기 수집·계산·파서 충돌에서 질문/interrupt 없이 아는 값만 사용한다. |
| 전역 보류 우선 | answer_status=abstained이면 개별 eligibility_status=충족이어도 모두 제외. 이 규칙은 API-14에만 적용. 일반 채팅 D6은 원래 카드를 보존하며 React는 전역 보류 시 자격 미확인으로 표시해야 한다(프론트 인계, 미구현). |
| 개별 자격 | 전역 보류가 아니면 eligibility_status=충족만 남긴다. 미확인·미충족·판정 누락은 제외. verification_unchecked가 남았다는 이유만으로 충족 카드를 버리지 않는다. |
| 지역 미입력 | region_scope="national"이고 region_names=["전국"]인 문서만 후보에 포함한다. 후보 수를 자르기 전에 대상 메타데이터를 확인한다. regional·unknown·대상 미상 문서 제외. 지역 입력이 있으면 기존 지역 규칙 적용. |
| 금액 미산정 | 프로필/계산 근거 부족으로 산정 불가하면 자격 충족 카드 유지. 계산 가능한 단일 금액·범위·총액은 보존. 산정 가능한 금액이 전혀 없는 카드의 금액 10필드는 null로 반환하고 화면 금액 영역은 숨긴다. |
| 금액 표현 정합성 | 숫자 필드 자체를 생략하지 않는다. amount=null만으로 숨기면 범위형 금액을 잃으므로 amount_min/max와 total_amount_min/max의 완전한 쌍도 본다. 총액만 미산정이면 amount_total 및 총액 범위만 null이고 알려진 단가·범위·라벨은 유지. |
| 최종 결과 정합성 | 필터 후 기존 정렬로 1..N 순위/배지를 부여하고 금액 표시를 정리. summary, 안내, 인용, output_json/text/markdown, 기존 세션 정책 캐시, duplicate_conflicts 모두 같은 최종 카드만 참조. 원문 조항은 유지하되 제외 카드의 추천/금액을 재노출하지 않는다. |
| 정상 0건 | HTTP 200, policies=[], final_answer="현재 정보로 추천할 정책이 없습니다". output_json.policies=[], summary 세 값 0, evidence_count=0, final_citations=[]. output_text/output_markdown도 같은 문구. 원래 answer_status 보존. |
| 오류와 0건 구분 | DB·검색·provider 최종 실패·deadline 소진을 정상 0건으로 바꾸지 않는다. RunPod 실패 후 남은 시간 안에 HF 성공은 정상 처리 가능. 자동 모드는 실제 provider 최종 오류를 GraphProviderError로 전파한다. 일반 채팅의 기존 비시간초과 폴백 정책은 유지한다. |
| 노드 총 90초 | LLM 호출 그래프 노드 1회 실행의 TOTAL 90초. RunPod/HF·모든 재시도·동시 병렬 호출이 하나의 deadline을 공유. HF에는 남은 시간만, 남은 시간 0이면 추가 호출 없음. 다른 노드 실행은 별도 90초. 요청 전체 한도가 아니다. |
| 한도 초과 처리 | 소진은 500 GRAPH_EXECUTION_ERROR로 전파하며 기존 재시도 안내. 별도 timeout code/status 없음. 규칙 폴백·일반 안내·0건으로 흡수하지 않는다. 호출자 대기 제한과 만료 후 상태 쓰기 차단을 적용하나 실행 중인 동기 SDK 스레드를 강제 종료하지 않는다. |

### Response — 에러

| HTTP 상태 | 에러 코드 | 발생 상황 | 메시지 예시 |
| --- | --- | --- | --- |
| 400 | VALIDATION_ERROR | 요청 본문/쿼리 전달 등 계약 위반 | 요청 형식이 올바르지 않습니다. |
| 401 | UNAUTHORIZED | 로그인 토큰 없음·만료·폐기 / 인증 계정 소멸 | 로그인이 필요합니다. |
| 503 | AUTH_BACKEND_UNAVAILABLE | 인증 확인 또는 저장 프로필 조회의 회원 DB 장애 | 기존 안전한 DB 오류 안내. 예: 회원 데이터베이스 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. |
| 503 | VECTOR_STORE_UNAVAILABLE | 검색 저장소·임베딩 제공자 장애 | 검색 서비스에 일시적으로 연결할 수 없습니다. |
| 500 | GRAPH_EXECUTION_ERROR | 전파된 그래프/LLM 최종 실패 또는 노드 총 90초 소진 | 일시적인 오류가 발생했습니다. 다시 시도해주세요. |
| 500 | INTERNAL_ERROR | 그래프 외 미분류 서버 오류 | 일시적인 오류가 발생했습니다. 다시 시도해주세요. |
| 404 (후속 API) | SESSION_NOT_FOUND | API-11/12: 채팅 세션 없음·다른 사용자 소유. API-14 자체는 세션 입력이 없음 | 세션이 만료되었거나 존재하지 않습니다. 새로 상담을 시작해주세요. |

### 에러 응답 형식

기존 ErrorResponse/ApiError 형태인 {code:string, message:string}를 사용한다. 이 API가 새로운 violations/remaining_seconds/timeout 필드를 추가하지 않는다. 실제 회원 DB 장애는 503이며 계정 소멸 401이나 채팅 세션 404와 구분한다.

후속 API의 기존 404 문구도 유지한다. API-11 SESSION_NOT_FOUND는 위 표의 메시지, API-12 SESSION_NOT_FOUND는 "세션이 만료되었거나 존재하지 않습니다."이다. API-12에서 최종 추천 캐시에 없는 정책은 POLICY_NOT_FOUND_IN_SESSION / "이 세션에서 추천된 정책이 아닙니다."이며 API-14 자체의 오류 코드는 아니다.

### 프론트엔드 구현 참고사항

| 책임 | React 인계 |
| --- | --- |
| 호출 시점 | API-02 200 및 브라우저 쿠키 설정 후 별도 POST 1회. API-08 프리필을 받아 전달하는 단계를 요구하지 않는다. 가입/페이지 재진입/프로필 수정에 자동 재호출을 추가하지 않는다. |
| 중복 방지 | 같은 로그인 성공 이벤트의 중복 effect/렌더 호출을 React의 현재 실행 상태로 막는다. 진행 중 버튼 중복 클릭도 막는다. 서버 멱등 저장소·캐시를 새로 만들지 않는다. 새로 로그인한 경우 새 요청·새 채팅 세션이다. |
| 로딩 / 카드 | 추천 로딩을 로그인 성공 상태와 분리. 200은 answered만 처리하며 순위와 검증 한글 라벨을 그대로 렌더. 전체 확인 완료로 과장하지 않는다. 상세·비교는 같은 policies 사용. |
| 금액 / 0건 | 금액/유효 범위가 모두 null인 카드의 금액 영역을 숨긴다. 0은 유효 값. 200 빈 목록은 정확한 안내 문구. 네트워크/HTTP 오류에 이 문구를 사용하지 않는다. |
| 실패 / 재시도 | 401은 재로그인 안내. API-14 새 실행 실패는 error 영역과 명시적 재시도 버튼으로 새 API-14 요청을 보낸다. API-11 그래프 실행 실패로 실패한 checkpoint는 같은 ID로 재시도할 수 없으며 API-10의 새 세션이 필요하다. 자동 반복 재시도·주기 갱신 없음. |
| 후속 질문 D4/D5 | 자동 추천 정상 완료 후 같은 ID의 API-11로 새 message를 보내면 자동 모드를 종료하고 일반 상담 새 턴을 시작한다. 계산 질문의 calc_slot_inputs와 calc_missing_choices를 표시하고 message 또는 calc_answers 중 하나로 제출. API-14 자체는 질문 없음. |
| 세션 분리 | 쿠키 이름 session_id는 인증 토큰, JSON session_id는 채팅 UUID. React는 쿠키를 읽거나 JSON ID를 인증 쿠키로 쓰지 않는다. 계정 전환·로그아웃·탈퇴 후 늦은 응답을 이전 화면에 붙이지 않는다. |

### 백엔드 구현 참고사항 — 세션 및 수명

| 항목 | 로컬 구현 / 한계 |
| --- | --- |
| 생성 / 소유권 | 요청마다 서버가 새 UUID 발급. 성공 결과 검증 뒤 인증 account ID에 소유권 등록. 0건도 완료된 세션으로 보존. API-11/12/13은 기존 소유권 검증. |
| D4 재사용 / 실패 한계 | 정상 완료 세션의 API-11 새 message는 자동 모드를 종료. initial_user_input·검색·계산·질문 횟수·선택지·결과를 초기화하고 신원/적절한 프로필을 보존한다. API-11 실패 checkpoint는 같은 ID 재시도 불가, API-10 새 세션 필요. checkpoint는 메시지 이력이 아니다. |
| 삭제 / 탈퇴 | API-13은 유효한 로그인에서 소유 세션을 삭제하며, 미존재·다른 사용자 소유 ID도 데이터 변경 없이 멱등 200을 반환한다. API-11/12의 미존재·다른 소유 ID는 404 SESSION_NOT_FOUND다. 탈퇴는 로그인 토큰·owner/profile/policy 캐시·checkpoint 정리 및 in-flight/늦은 완료의 상태 재생성 방지. 다른 사용자와 실패한 탈퇴 상태 보존. |
| 실패한 새 실행 | 실제로 실행한 graph 인스턴스의 checkpoint와 부분 소유권을 정리. 정리 중 다른 graph를 생성하지 않으며 원래 오류를 가리지 않는다. 실패한 호출은 유효한 채팅 session_id를 성공 응답으로 내리지 않는다. |
| 기존 한계 | 외부 checkpoint 삭제가 실패할 수 있어 로그에 남긴다. 완전 삭제나 실행 스레드 강제 중단을 보장하지 않는다. 기존 단일 프로세스 메모리 세션/캐시의 재시작 유실 한계 유지. |
| 로그 / 미결 범위 | D11: RunPod 서버/인증 실패를 HF 성공 여부와 무관하게 기록한다. HTTP 상태 및 짧은 오류 코드/유형만 남기고 토큰·쿠키·프로필·프롬프트·원시 모델 출력은 기록하지 않는다. 새 winning-provider 필드 없음. API-12 D9/D10은 기존 PM 미결 계약 그대로다. |

## 계약참조  필드·예시·원본 추적

v1.0  ·  고정 소스 532a5548  ·  공용 D5 및 API-11 연계

### 가입 정보와 그래프 필수 정보

가입 필수 계정 필드는 email, password, password_confirm, name, terms_agreed, privacy_agreed 6개. 선택 프로필은 region, gender, birth_date, disability_status, income_bracket, household_types, veteran_status, interests 8개. 그래프 필수 6개는 region, birth_date, gender, income_bracket, disability_status, employment_status이며 그중 5개만 가입에서 선택적으로 수집한다.

### 서버 저장 프로필 매핑

| DB 필드 / 서버 매핑 | 구분 | 처리 |
| --- | --- | --- |
| region → known_region | 선택 / hard gate | 누락이면 unknown. 전국 대상만 추천. |
| birth_date → known_birth_date | 선택 / hard gate | 누락이면 unknown. 있으면 Asia/Seoul 기준 만 나이 파생. 엄격한 YYYY-MM-DD 및 만 120세 전체 연도(121번째 생일 전까지) 경계 적용. age를 별도 사용자 입력으로 받지 않는다. |
| gender → known_gender | 선택 / hard gate | 누락이면 unknown. |
| income_bracket → known_income_bracket | 선택 / hard gate | 누락이면 unknown. |
| disability_status → known_disability_status | 선택 / hard gate | 누락이면 unknown. 장애 미등록으로 추정 금지. |
| employment_status | 가입 미수집 / hard gate | DB 가입 필드가 없다. unknown 유지, 무직/취업으로 지어내거나 질문하지 않는다. |
| household_types → known_household_types | 선택 | 없으면 빈 목록. 자녀 수/가구원 수를 임의 추정하지 않는다. |
| veteran_status → known_veteran_status | 선택 | 자동 모드는 저장된 유효 보훈 상태를 슬롯에 유지한다. registered는 기존 보훈 검색 힌트도 추가. 판정 근거를 새로 만들지 않는다. |
| interests → extra_interests | 선택 | 관심 지원조건은 검색 힌트. 단독 자격 확정 근거가 아니다. 서버의 현재 후보 수 기본값 5 재사용; 클라이언트 입력 없음. |

### PolicyView — policies[] 전체 명시 필드

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| rank | integer\|null | 필터 후 기존 자격·금액 정렬 규칙으로 1부터 연속 부여. 금액 표시 정리에서 숫자를 새로 추정하지 않는다. |
| policy_id | string | 정책 고유 ID. API-12의 policy_id와 연결. |
| title | string | 정책명. |
| badge | string\|null | 기존 한글 배지 규칙. 최종 순위를 반영하고 제거된 카드의 우선 검토 배지를 남기지 않는다. |
| eligibility_status | string\|null | 자동 추천에 포함된 카드는 충족. 일반 채팅의 원래 판정은 변경하지 않는다. |
| eligibility_reasons | string[] | 판정 근거. |
| verification_checked | string[] | 실제로 대조한 조건의 한글 라벨. |
| verification_unchecked | string[] | 추가 확인이 필요한 조건의 한글 라벨. 비어 있지 않아도 충족 카드는 유지. |
| verification_note | string\|null | 검증 범위 안내. 모든 조건이 확인됐다고 단정하지 않는다. |
| amount | number\|null | 산정 가능한 지원금 숫자. 금액이 0으로 계산되면 0 보존. 범위만 알면 null 가능. |
| amount_label | string\|null | 근거 있는 금액·범위·주기·상한 표현. 금액 표시가 불가능하면 null. |
| amount_period | string\|null | month/year/once 등 기존 값. 금액 표시 불가능 시 null. |
| amount_is_maximum | boolean\|null | 상한 여부. 미산정 시 null. false를 미산정 표시로 대체 사용하지 않는다. |
| amount_per_unit | string\|null | 1인당·가구당 등 기존 단위. 미산정 시 null. |
| amount_total | number\|null | 근거 있는 총액만 반환. 개별 금액을 알아도 총액 계산 정보가 없으면 null. |
| amount_min | number\|null | 근거 있는 범위 하한. amount_max와 쌍으로 유효. |
| amount_max | number\|null | 근거 있는 범위 상한. amount_min과 쌍으로 유효. |
| total_amount_min | number\|null | 계산 가능한 총액 범위 하한. total_amount_max와 쌍으로 유효. |
| total_amount_max | number\|null | 계산 가능한 총액 범위 상한. total_amount_min과 쌍으로 유효. |
| duplicate_status | string\|null | 기존 가능/조건부/미확인 등 판정. D8에 따라 중복 제한 근거를 불가로 자동 격상하지 않는다. |
| duplicate_note | string\|null | 실제 중복수급 조항의 근거 문구 보존. 지원금 미산정 사유를 여기에 넣지 않는다. |
| duplicate_clause_kind | string\|null | other/household/header/null 등 기존 조항 구분. |
| duplicate_conflicts | object[] | 같은 최종 응답 안의 상대 정책만 {policy_id:string, title:string}로 연결. 제외 카드 ID/제목 연결은 제거하고 독립된 실제 조항 원문은 보존. |
| household_limit_clauses | string[] | 가구별 신청 제한 원문. 중복수급과 구분해 표시. |
| needs_confirmation | string[] | 검증 한계·금액 미산정 사유 등 수동 참고 안내. 자동 모드의 입력 요구/되묻기 UI로 사용하지 않는다. |
| related_law | object[] | 기존 관련 법령 사전 구조. metadata_only 법령을 조문 본문으로 해석하지 않는다. |
| detail | PolicyDetail | 목록·상세·비교에서 재사용. 아래 20개 명시 필드와 기존 extra 허용 유지. |

### PolicyDetail — policies[].detail 전체 명시 필드

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| purpose | string\|null | 사업 목적 원문. |
| support_target | string\|null | 지원 대상 원문. |
| eligibility_criteria | string\|null | 선정 기준 원문. |
| support_details | string\|null | 지원 내용 원문. 개인 산정 금액이 아니며 원문의 숫자를 개인 금액으로 재표시하지 않는다. |
| application_method | string\|null | 신청 방법 원문. |
| application_period | string\|null | 신청 기한 원문. |
| legal_basis | string\|null | 근거 법령 원문/메타데이터의 범위 보존. |
| region_names | string[]\|null | 정책 대상 지역. 전국 대상은 [전국]. 지역 미입력 시 national 및 전국 대상이 검증된 문서만 허용. |
| region_scope | string\|null | national/regional/unknown 등 기존 메타데이터. 기관 소재지를 전국 대상의 근거로 쓰지 않는다. |
| age_start | integer\|null | 대상 연령 하한. |
| age_end | integer\|null | 대상 연령 상한. |
| organization | string\|null | 소관 기관. |
| source_url | string\|null | 원문 URL. |
| source_name | string\|null | 출처명. |
| required_documents | string\|null | 일반 구비서류 원문 그대로. |
| required_documents_items | string[]\|null | 일반 구비서류의 기존 규칙 기반 파싱 배열. 원문 없으면 null. |
| required_documents_official | string\|null | 공무원 확인 구비서류 원문 그대로. |
| required_documents_official_items | string[]\|null | 공무원 확인 서류 파싱 배열. 원문 없으면 null. |
| required_documents_self | string\|null | 본인 확인 필요 구비서류 원문 그대로. |
| required_documents_self_items | string[]\|null | 본인 확인 서류 파싱 배열. 원문 없으면 null. |

### output_json — 현재 서비스 표현과 정합성

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| status / session_id<br>answer_status / final_answer | 상위 필드와 동일 | 최상위 값과 정확히 일치. |
| summary.checked | integer | len(policies). 필터 전 검색 후보 수가 아니다. |
| summary.eligible | integer | 자동 추천에선 len(policies). |
| summary.not_eligible_or_unknown | integer | 자동 추천 최종 목록에서는 0. 미확인 카드가 확인 완료됐다는 뜻이 아니다. |
| profile | object[] | DB에서 실제 알고 있는 정보만 기존 표시 형태로 변환. 원본 생년월일/인증 식별자는 출력하지 않고 만 나이만. |
| profile[].key / label / value | 모두 string | 기존 슬롯 키·한글 라벨·표시값. 누락값에 대한 가상의 항목을 생성하지 않는다. |
| evidence_count | integer | len(final_citations). 0건은 0. |
| final_citations | object[] | 최상위 final_citations와 동일한 배열. |
| policies | PolicyView[] | 최상위 policies와 동일한 최종 카드·세부 필드. 구비서류 *_items와 null 금액까지 일치. |
| interrupt_id / calc_missing_slots<br>calc_missing_choices / calc_slot_inputs | 공용 필드와 동일 | D5 needs_input 응답의 output_json에도 동일한 계산 필드 제공. 532a5548의 answered output_json은 이 4개 키를 별도 추가하지 않는다. API-14 최상위 4개 필드는 null/[]/[]/[]로 직렬화한다. |

### D5 공용 CalculationSlotInput — calc_slot_inputs[]

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| slot | string | 현재 질문이 요구하는 슬롯 키. marital_status, pregnancy_status, children_count, household_size 중 해당 항목. age 입력 없음. |
| label | string | 기존 _CALC_SLOT_LABELS의 한글 표시명. 아래 실제 슬롯 표 참조. |
| input_type | string | select 또는 number. 카테고리에는 select, 정수 입력에는 number. |
| options | object[] | select는 {value:string, label:string} 목록. value를 전송하고 한글 label을 표시. number는 []. |
| minimum | integer\|null | number의 포함 하한. children_count=0, household_size=1. select는 null. |
| maximum | integer\|null | number의 포함 상한. children_count=20, household_size=30. select는 null. |

### D5 공용 CalculationChoice — calc_missing_choices[]

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| policy_id | string | 현재 질문의 정책 ID. 선택 답변을 이 키에 바인딩한다. |
| labels | string[] | 현재 정책에서 제시한 원문 선택지 목록. 선택한 문자열을 변형 없이 전송한다. |
| policy_title | string | 정책별 제목. 코어에서 제목을 찾지 못하면 policy_id로 대체하는 기존 동작. |

### D5 실제 슬롯·선택 코드·한글 라벨

| 슬롯 | input_type | 입력 규약 |
| --- | --- | --- |
| marital_status | select | 혼인 상태 (미혼/기혼/이혼/사별). single=미혼, married=기혼, divorced=이혼, bereaved=사별. unknown/모름은 선택값 아님. |
| pregnancy_status | select | 임신/출산 상태. pregnant=임신 중, postpartum=산후, none=해당 없음. unknown/모름은 선택값 아님. |
| children_count | number | 자녀 수. 0..20 포함, 엄격한 정수만. bool/소수/숫자 문자열 불가. |
| household_size | number | 가구원 수. 1..30 포함, 엄격한 정수만. bool/소수/숫자 문자열 불가. |

### 숫자 입력 범위와 나이

children_count 0..20, household_size 1..30은 기존 파서에서 재사용한 입력 경계이며 새로운 급여 계산 규칙이 아니다. age는 생년월일로 파생하므로 calc_answers.slots에서 받지 않는다. 프론트는 현재 calc_slot_inputs에 나타난 항목만 입력받는다.

### API-11 공용 FollowupRequest — 기존 경로 유지

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| message | string\|null | 공백이 아닌 자유 텍스트. calc_answers가 null/생략일 때만 사용. 계산 중 message="모름"으로 기존 건너뛰기 동작 유지. |
| calc_answers | CalculationAnswers\|null | 계산 interrupt에 대한 구조화 답변. message가 null/생략일 때만 사용. 둘 중 정확히 하나가 non-null이어야 한다. |

### API-11 공용 CalculationAnswers — calc_answers

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| interrupt_id | string | 필수, 공백 불가. 가장 최근 계산 질문에서 받은 실제 ID. 다른/이전 ID 또는 일반 프로필 질문에 사용하면 400. |
| slots | object | 생략 시 {}. 현재 질문에 있는 슬롯만. 카테고리는 실제 enum 문자열, 숫자는 엄격한 정수. |
| choices | object | 생략 시 {}. {policy_id: exact_label}. 현재 질문의 정책 ID와 labels에 있는 정확한 문자열만. slots/choices 중 최소 한 항목 필요. |

### API-11 검증·부분 응답·재개

POST /api/v1/chat/sessions/{session_id}/followup의 본문은 non-null message 또는 non-null calc_answers 중 정확히 하나다. 둘 다 non-null이거나 둘 다 null/생략이면 400 VALIDATION_ERROR. calc_answers 안의 추가 키는 금지한다. slots/choices는 생략 가능하지만 합쳐서 최소 한 답변이 있어야 한다.

숫자 슬롯의 bool·소수·숫자 문자열·범위 초과, 미지/현재 질문에 없는 슬롯, age, 내부 unknown, 잘못된 enum, 다른 정책 ID·정확히 일치하지 않는 라벨, 오래된 interrupt_id는 400 VALIDATION_ERROR. 혼합 답변 중 하나라도 잘못되면 graph.invoke 전에 전체 거절하며 checkpoint를 변경하지 않는다. interrupt_id는 최신 질문 연결용이며 권한은 인증·소유권·잠금으로 검증한다.

일부 답변만 제출할 수 있다. 다시 질문을 받으면 새 interrupt_id, calc_slot_inputs, calc_missing_choices를 사용한다. 이전 ID 재전송 금지. 400 거절은 상태를 바꾸지 않는다. message="모름"은 기존 계산 건너뛰기이며 structured enum에 unknown/모름을 추가하지 않는다.

정상 완료된 상담의 새 message는 D4에 따라 같은 ID에서 새 턴을 실행한다. API-11 그래프 실패로 실패한 checkpoint는 같은 ID로 재실행하지 못하므로 API-10 새 세션으로 시작한다. API-14 초기 실행 실패는 새 API-14 요청으로 재시도한다. 이전 문답을 메시지 이력으로 전달한다는 계약은 없다.

### 요청 예시 — 본문 없음

기존 브라우저 로그인 쿠키가 전송된다. 실제 토큰·사용자 ID·프로필·질문을 예시에 넣지 않는다.

### React 요청 예시

```javascript
const response = await fetch("/api/v1/chat/recommendations", {
  method: "POST",
  credentials: "include"
});
```

### 합성 예시의 범위

아래는 실제 정책·회원·실행 로그가 없는 JSON 계약 예시다. llm_status/timing의 {}는 스키마상 허용되는 빈 진단 객체이며 실서비스 결과를 주장하지 않는다. policy 예시는 개별 PolicyView 객체이며 전체 HTTP 응답으로 오해하지 않는다.

### 응답 예시 — 200 OK / 전역 abstained / 0건

```json
{
  "status": "answered",
  "session_id": "00000000-0000-4000-8000-000000000014",
  "question": null,
  "missing_slots": [],
  "interrupt_id": null,
  "calc_missing_slots": [],
  "calc_missing_choices": [],
  "calc_slot_inputs": [],
  "slot_conflicts": null,
  "answer_status": "abstained",
  "final_answer": "현재 정보로 추천할 정책이 없습니다",
  "final_citations": [],
  "policies": [],
  "output_json": {
    "status": "answered",
    "session_id": "00000000-0000-4000-8000-000000000014",
    "answer_status": "abstained",
    "final_answer": "현재 정보로 추천할 정책이 없습니다",
    "summary": {"checked": 0, "eligible": 0, "not_eligible_or_unknown": 0},
    "profile": [],
    "evidence_count": 0,
    "final_citations": [],
    "policies": []
  },
  "output_text": "현재 정보로 추천할 정책이 없습니다",
  "output_markdown": "현재 정보로 추천할 정책이 없습니다",
  "llm_status": {},
  "timing": {}
}
```

### 정책 객체 예시 — 충족 + 검증 미완료 + 금액 미산정

```json
{
  "rank": 1,
  "policy_id": "sample-policy-014",
  "title": "가상 지원 정책",
  "badge": "우선 검토",
  "eligibility_status": "충족",
  "eligibility_reasons": ["제공된 정보로 자격 조건 확인"],
  "verification_checked": ["지역"],
  "verification_unchecked": ["추가 서류"],
  "verification_note": "대조하지 못한 항목은 별도 확인이 필요합니다.",
  "amount": null,
  "amount_label": null,
  "amount_period": null,
  "amount_is_maximum": null,
  "amount_per_unit": null,
  "amount_total": null,
  "amount_min": null,
  "amount_max": null,
  "total_amount_min": null,
  "total_amount_max": null,
  "duplicate_status": "미확인",
  "duplicate_note": null,
  "duplicate_clause_kind": null,
  "duplicate_conflicts": [],
  "household_limit_clauses": [],
  "needs_confirmation": ["금액 계산 정보가 부족합니다."],
  "related_law": [],
  "detail": {
    "purpose": "문서용 가상 정책",
    "support_target": "전국 대상",
    "eligibility_criteria": null,
    "support_details": null,
    "application_method": null,
    "application_period": null,
    "legal_basis": null,
    "region_names": ["전국"],
    "region_scope": "national",
    "age_start": null,
    "age_end": null,
    "organization": null,
    "source_url": null,
    "source_name": "합성 예시",
    "required_documents": null,
    "required_documents_items": null,
    "required_documents_official": null,
    "required_documents_official_items": null,
    "required_documents_self": null,
    "required_documents_self_items": null
  }
}
```

### 계산 가능한 금액 예시

위 카드에서 amount=100000, amount_label="월 100,000원", amount_period="month", amount_is_maximum=false로 계산됐다면 그대로 유지한다. 총액 근거가 없으면 amount_total/total_amount_min/total_amount_max만 null을 유지한다. 금액이 없다는 이유로 카드 자체를 제거하지 않는다. 금액 미산정 예시의 10개 금액 필드는 실제 스키마에 있는 키이며 생략된 키가 아니다.

### 응답 예시 — 500 GRAPH_EXECUTION_ERROR / deadline 포함

```json
{"code": "GRAPH_EXECUTION_ERROR", "message": "일시적인 오류가 발생했습니다. 다시 시도해주세요."}
```

### 후속 질문 예시 — 정상 완료 뒤 사용자 직접 입력

POST /api/v1/chat/sessions/00000000-0000-4000-8000-000000000014/followup. 동일 로그인 쿠키 필요. 자동 추천 정상 완료 후 같은 ID에서 일반 상담 새 턴으로 전환한다. 이때부터 일반 상담의 질문 규칙이 적용된다. API-14 최초 실행 안에서는 질문하지 않는다.

### 후속 API-11 Request Body 예시

```json
{"message": "주거 지원 정책을 더 알려주세요"}
```

### D5 계산 질문 예시의 범위

다음 JSON은 일반 상담의 계산 질문 응답 중 공용 필드 발췌다. API-14 응답 예시가 아니다. 고정 ID·정책·선택 가/나는 무해한 합성값이며 실제 제출에는 방금 받은 interrupt_id/policy_id/labels를 사용한다. 모델의 필드명·카테고리 코드·한글 라벨·정수 경계는 532a5548의 실제 정의와 같다.

### API-11 계산 질문 응답 발췌 예시

```json
{
  "status": "needs_input",
  "session_id": "00000000-0000-4000-8000-000000000014",
  "interrupt_id": "11111111111111111111111111111111",
  "calc_missing_slots": ["marital_status", "pregnancy_status", "children_count", "household_size"],
  "calc_missing_choices": [{"policy_id": "sample-policy-014", "labels": ["선택 가", "선택 나"], "policy_title": "가상 지원 정책"}],
  "calc_slot_inputs": [
    {
      "slot": "marital_status",
      "label": "혼인 상태 (미혼/기혼/이혼/사별)",
      "input_type": "select",
      "options": [
        {"value": "single", "label": "미혼"},
        {"value": "married", "label": "기혼"},
        {"value": "divorced", "label": "이혼"},
        {"value": "bereaved", "label": "사별"}
      ],
      "minimum": null,
      "maximum": null
    },
    {
      "slot": "pregnancy_status",
      "label": "임신/출산 상태",
      "input_type": "select",
      "options": [
        {"value": "pregnant", "label": "임신 중"},
        {"value": "postpartum", "label": "산후"},
        {"value": "none", "label": "해당 없음"}
      ],
      "minimum": null,
      "maximum": null
    },
    {
      "slot": "children_count",
      "label": "자녀 수",
      "input_type": "number",
      "options": [],
      "minimum": 0,
      "maximum": 20
    },
    {
      "slot": "household_size",
      "label": "가구원 수",
      "input_type": "number",
      "options": [],
      "minimum": 1,
      "maximum": 30
    }
  ]
}
```

### API-11 부분 계산 답변 Request Body 예시

```json
{
  "calc_answers": {
    "interrupt_id": "11111111111111111111111111111111",
    "slots": {"marital_status": "married", "children_count": 2},
    "choices": {"sample-policy-014": "선택 가"}
  }
}
```

### 부분 답변 이후

위 제출은 pregnancy_status·household_size를 답하지 않은 부분 제출이다. 서버가 남은 항목을 다시 질문하면 응답의 새 interrupt_id로 이어서 제출한다. 예시 ID를 고정값으로 재사용하지 않는다.

### API-11 자유 텍스트 계산 건너뛰기 예시

```json
{"message": "모름"}
```

### 원본 위치와 최종 결정 추적

| 원본 문서 / 위치 | 변경·보완 계약 | 결정 출처 / 근거 |
| --- | --- | --- |
| 요구사항_정의서.xlsx · S-03!A12:H12 (S03-07) | 기존 화면 진입 API-08 → 첫 질문 API-10 프리필을 로그인 성공 후 API-14로 확장. 클라이언트 프로필 전달 대신 서버 DB 조회. | 최종 사용자 승인 / 백엔드 계약 추적표의 API-14 행. 자동 추천 신규, 일반 수동 상담 API 유지. |
| API_정의서.xlsx · INDEX!A5:A17, API-02!A2:D15 | 원본 13개 ID 뒤 API-14 추가. 로그인 요청/응답·Set-Cookie는 그대로. | v0.1 승인된 엔지니어링 계약. 추천은 별도 POST. |
| API_정의서.xlsx · API-08!A15:D23, A30:A35 | 프리필 매핑 재사용. employment 질문 필수라는 과거 설명은 자동 추천에 적용하지 않음. | 사용자 승인 API-14. 누락 unknown, 초기·계산 질문 없음. |
| API_정의서.xlsx · API-01!A8:D21; 요구사항_정의서.xlsx · S-02!A6:H7 | 가입 필수 계정 6개와 선택 프로필 8개 구분. 그래프 필수 6개 중 5개가 가입 선택 항목이며 employment만 미수집. | 현재 schemas/auth.py 및 slot_schema.py:150–158. 6개에 5개를 더한 구조가 아님. |
| API_정의서.xlsx · API-10!A25:D36, A48:A54 | 공용 응답 재사용, 자동 모드는 answered만 반환. v0.2의 D5 공용 필드 4개 유지. | 532a5548 schemas/chat.py:220–240 및 service.py:1099–1196. API-14 전용 새 필드 0개. |
| 요구사항_정의서.xlsx · S-06!A6:H7 | 충족만 남기되 전역 abstained가 최우선. 필터 후 통계·순위·라벨·금액 재작성. | 사용자 승인 API-14 및 D6. 일반 채팅 보류 카드는 보존. |
| 요구사항_정의서.xlsx · S-07!A8:H8 | 산정 불가 금액은 화면 생략. 충족 카드는 유지. 중복 불가 자동 격상은 조건부로 정정. | 사용자 승인 API-14 및 D8. 532a5548 service.py:1082–1096의 null 직렬화. |
| API_정의서.xlsx · API-10!A38:G38; 요구사항_정의서.xlsx · S-07!A11:H11, S-10!A6:H6 | 구비서류 원문 3개 + *_items 3개 배열 유지. 과거 보류 설명은 최종 옵션 ②로 해소. | 구비서류 옵션 ② 승인 및 현재 [schemas/chat.py](../backend/app/schemas/chat.py). |
| API_정의서.xlsx · API-11!A2:D20, A26:E29; API-13!A17:E24 | 정상 완료 후 같은 세션의 새 질문은 일반 상담 새 턴. 자동 모드는 종료. API-11 실패 checkpoint는 같은 ID 재시도 불가, API-10 새 세션 필요. | 532a5548 builder.py:511–540, chat_adapter.py::continue_chat/delete_chat_session. 401/404/삭제 200 유지. |
| API_정의서.xlsx · API-11!A14:D20, A26:G29, A31:G33; API-10!A25:D36 | v0.1 → v0.2: message 단독을 non-null message XOR calc_answers로 확장한 D5. v1.0에서 동일 공용 응답·입력 정의 유지. 원본은 수정하지 않음. | 532a5548 schemas/chat.py:108–127, request_calc_info.py, builder.py:519–526. 슬롯/선택지 오류는 400 VALIDATION_ERROR. |
| API_정의서.xlsx · API-10!A42:G45 | 90초 노드 총 한도 소진도 기존 500 GRAPH_EXECUTION_ERROR와 재시도 안내 재사용. | [노드 총 실행 한도](RUNPOD_SETUP_DRAFT.md#노드-총-실행-한도)의 최종 승인 규칙. chat_adapter.py::_run. 옛 180초/제공자별 90초 폐기. |
| PROJECT_STRUCTURE.md · §§0, 2.2, 3 | React/FastAPI 역할·chat 라우터·기존 응답 재사용. 과거 코어 전체 변경 금지는 승인된 자동 모드/공유 코어 변경 범위에서 대체됨. | 파일 구조의 참고 자료이며 최신 업무 결정의 출처와 구분. |
| 사용자 승인 및 [백엔드 계약 추적표](../backend/README.md#원본-문서와-남은-계약-차이) | 원본과 승인된 변경을 구분하고 구현·검증·담당을 추적한다. | v1.0 코드 기준 `532a5548`. 배포/실연동 완료 선언 아님. D9/D10 별도 PM 미결. |

### 프론트엔드 인계 및 통합 확인 항목

| 항목 | 연동 확인 기준 |
| --- | --- |
| 인증 / 입력 | 본인 DB 프로필만 사용. body/query 거절. 무효/소멸 계정 401, DB 장애 503. 로그인 응답 불변. |
| 필수값 부족 | 가입 선택 8개가 모두 없어도 초기/계산 질문 없음. 취업 상태 unknown 보존. 전국 대상만 후보. |
| 필터 우선순위 | 충족+verification_unchecked 유지, 미확인/미충족 제외, abstained+충족도 전부 제외. 일반 채팅 D6 카드 유지. |
| 금액 / 표시 | 미산정 10필드 null 및 금액 영역 미표시, 알려진 0·숫자·유효 범위 보존, 단가만 알면 총액 null. 제거 카드의 금액/인용/중복 링크 잔존 없음. |
| 0건 / 오류 | 정상 0건 정확한 문구·200·빈 배열·0 요약. DB/provider/deadline 오류는 정상 0건과 분리. |
| 90초 / 병렬 | 노드별 하나의 절대 deadline에 RunPod·HF·재시도·병렬 호출 모두 포함. 다음 노드는 새 한도. 소진 시 GRAPH_EXECUTION_ERROR. |
| 생명주기 | API-14 성공/빈 결과의 소유 세션, 실패 정리, 탈퇴 후 대기 요청 차단을 구현했다. 실제 배포 환경에서 확인한다. API-11 정상 새 턴의 자동 모드 종료와 실패 checkpoint 재사용 불가를 프론트 흐름에 반영한다. |
| React | 로그인 성공 후 1회, 중복 호출 차단, loading/cards/empty/error 구분, explicit retry, 쿠키와 채팅 ID 분리. |
| 검증 한계 | 실제 원격 DB·LLM·React 연동 및 실제 90초 대기는 미검증. 동기 SDK 스레드를 강제 종료하지 않아 고정 실행 슬롯 점유·프로세스 종료 지연이 가능하다. API-12 D9/D10은 기존 PM 미결 유지. |

### 출처와 검증 범위

고정 소스 기준은 `532a5548db28275959891e2f25f0459464634704`, API-14 구현은 `6b40aeedb8498db52ef54ee617ac13d997933afc`다. 외부 v1.0 Markdown/XLSX 정의서에서 계약·필드·JSON 예시를 유지하고 저장소 링크와 진행 상태 참조만 정리했다. Downloads 원본과 외부 v0.1/v0.2/v1.0은 수정하지 않았다.

[백엔드 계약 추적표 및 검증 범위](../backend/README.md#원본-문서와-남은-계약-차이)가 현재 승인 결정·구현·담당의 기준이다. 구현과 독립 검증 수용은 배포·실연동 완료를 뜻하지 않는다. [HTTP 자동 추천 검사](../backend/tests/test_auto_recommendation.py), [코어 자동 추천 검사](../tests/test_auto_recommendation.py), [계산 입력 검사](../backend/tests/test_calc_followup.py)가 실행 가능한 회귀 근거다.

실제 원격 DB·LLM·React 연동과 실제 제공자의 90초 대기는 미검증이다. 노드 한도는 축소 시간·가짜 제공자로 검증했다. HF 최소 요구 버전 `huggingface_hub>=0.29.0`은 소스 호환 검토 근거이며 실행 검증은 설치된 `1.31.0`이다. `_inner_post` 내부 훅과 임의 미래 버전 호환성을 보장하지 않는다. 자세한 운영 한계는 [LLM 설정](RUNPOD_SETUP_DRAFT.md#노드-총-실행-한도)을 따른다.
