import { useQuery } from "@tanstack/react-query";
import { getChatProgress } from "@/api/chatApi";
import type { ChatProgress } from "@/types/chat";

/**
 * 진행률 폴링 간격. 노드 하나가 보통 수 초 이상 걸리므로 1초 남짓이면
 * 막대가 끊겨 보이지 않으면서도 서버에 부담을 주지 않는다.
 */
const POLL_INTERVAL_MS = 1200;

/**
 * 상담이 도는 동안 백엔드의 진행 상황을 폴링한다(S03-06 확장).
 *
 * 왜 폴링인가: API-10/11/14는 요청 하나가 끝날 때까지 응답이 없는 동기
 * 엔드포인트라, 그 요청만으로는 "지금 어디쯤인지"를 알 수 없다. 백엔드가
 * 같은 정보를 별도 GET으로 열어 두었으므로(진행률 조회 API) 그쪽을 짧은
 * 간격으로 읽는다.
 *
 * ``token``은 요청 한 건을 가리키는 클라이언트 생성 값이다(useChatSession이
 * 요청마다 새로 만든다). ``active``가 false면 폴링하지 않는다 - 요청이 끝난
 * 뒤에도 계속 읽으면 의미 없는 트래픽만 남는다.
 *
 * 실패는 조용히 삼킨다(retry:false, 오류를 밖으로 내보내지 않음). 진행률은
 * 보조 정보라, 이게 안 된다고 상담 화면에 오류 배너를 띄우면 안 된다.
 */
export function useChatProgress(token: string | null, active: boolean): ChatProgress | null {
  const { data } = useQuery({
    queryKey: ["chat-progress", token],
    queryFn: () => getChatProgress(token as string),
    enabled: Boolean(token) && active,
    refetchInterval: POLL_INTERVAL_MS,
    refetchOnWindowFocus: false,
    retry: false,
    gcTime: 0,
    staleTime: 0,
  });

  // 요청이 끝나면(active=false) 마지막 스냅샷이 캐시에 남아 있어도 막대를
  // 그리지 않는다 - 결과 화면 위에 100% 막대가 남아 있을 이유가 없다.
  return active ? data ?? null : null;
}
