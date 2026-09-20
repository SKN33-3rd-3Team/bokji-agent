# 진행률·홈 캐시 브라우저 회귀 검사

Vite와 Microsoft Edge, Node에서 불러올 수 있는 `playwright`가 필요합니다.
로컬 설치는 `npm install --no-save --package-lock=false playwright`로 준비하거나,
이미 설치된 공용 패키지 경로를 `NODE_PATH`로 지정합니다.

```powershell
# frontend에서 별도 터미널
npm run dev -- --host 127.0.0.1 --port 5178

# frontend에서 실행
node tests/progress-cache.cjs
node tests/chat-results.cjs
```

다른 주소는 `TEST_BASE_URL` 환경변수로 지정합니다. 테스트는 모든 API를
공개 가상 데이터로 대체하며 실제 회원 DB나 LLM을 호출하지 않습니다.

검증 범위:
- 회원가입에는 필수 동의 체크박스 두 개만 표시
- 홈·채팅에서 POST에 실은 토큰으로 진행률 GET을 반복 조회하고 표시 갱신
- 홈 추천 요청 중 화면을 떠났다 돌아와도 기존 요청·진행률 유지
- 홈에서 새 상담으로 이동해도 추천 세션을 삭제하지 않음
- 홈 재방문 시 추가 추천 POST 없이 캐시 결과 재출력
- 추가 상담 전송 중·완료 후에도 이전 정책 카드와 비교 선택 유지
- 이전 결과의 상세 보기 및 정책 카드 배지·칩 높이 정렬
- 첫만남 이용권 상세 문의의 명령형 입력 두 가지를 API에 그대로 전달

추천 캐시는 현재 탭의 앱 메모리에 유지됩니다. 페이지 전체 새로고침,
로그아웃 또는 프로필 변경 시 초기화합니다. 채팅 초기화는 홈 캐시에
영향을 주지 않습니다. 실제 DB·벡터 검색·LLM 연동은 별도 검증입니다.
