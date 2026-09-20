import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getAutoRecommendations } from "@/api/chatApi";
import { newProgressToken } from "./useChatSession";
import { useAuth } from "@/features/auth/useAuth";
import { ApiError } from "@/api/client";

/** 자동 추천 결과 캐시 키. 회원 한 명당 하나 - 계정이 바뀌면 통째로 비운다. */
export const AUTO_RECOMMENDATION_QUERY_KEY = ["auto-recommendations"];

/**
 * API-14(자동 추천) 결과를 캐시해, 홈 화면에 다시 들어와도 재검색하지 않는다.
 *
 * 왜: 이 호출은 그래프 N1~N14를 통째로 도는 무거운 요청이다(실측 2분대).
 * 예전에는 홈에 들어올 때마다 매번 새로 돌려서, 마이페이지 다녀오거나 로고를
 * 누르기만 해도 같은 결과를 몇 분씩 다시 기다려야 했다.
 *
 * ``staleTime``/``gcTime``을 무한으로 두고 ``refetchOnMount``를 끈다 - 즉
 * **캐시가 살아 있는 동안은 네트워크를 아예 타지 않는다.** 대신 결과가 낡는
 * 시점마다 캐시를 지워 다음 진입에서 새로 받게 한다(``useResetAutoRecommendations``):
 * 프로필 수정(API-05), 상담 세션 초기화(API-13), 로그아웃.
 *
 * ``invalidateQueries``가 아니라 제거(``removeQueries``)를 쓰는 이유는
 * ``refetchOnMount: false``와 같이 두면 "stale 표시"만으로는 다시 안 받기
 * 때문이다 - 엔트리를 없애야 다음 마운트에서 실제로 새로 받는다.
 */
export function useAutoRecommendations(enabled: boolean) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const queryKey = [...AUTO_RECOMMENDATION_QUERY_KEY, user?.id];
  const progressKey = [...queryKey, "progress-token"];
  // 요청 중 다른 화면에 갔다 돌아와도 같은 요청의 진행률을 이어서 조회한다.
  const { data: progressToken = null } = useQuery<string | null>({
    queryKey: progressKey,
    queryFn: () => null,
    enabled: false,
    gcTime: Infinity,
  });

  const query = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const token = newProgressToken();
      queryClient.setQueryData(progressKey, token);
      return getAutoRecommendations(token, signal);
    },
    enabled: enabled && Boolean(user),
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    // 개발 서버 --reload가 모델 캐시/런타임 변경을 감지하면 진행 중인
    // 그래프가 종료되며 500/503이 올 수 있다. 연결 끊김과 이 두 일시 오류만
    // 재시도한다. 인증·입력 검증 같은 영구 오류는 즉시 표시한다.
    retry: (failureCount, error) => {
      if (!(error instanceof ApiError) || failureCount >= 2) return false;
      return error.status === 0 || error.status === 500 || error.status === 503;
    },
    retryDelay: (attempt) => Math.min(1000 * (attempt + 1), 2500),
  });

  return { ...query, progressToken };
}

/** 캐시를 버려 다음 홈 진입에서 새로 받게 한다. */
export function useResetAutoRecommendations() {
  const queryClient = useQueryClient();
  return useCallback(
    () => queryClient.removeQueries({ queryKey: AUTO_RECOMMENDATION_QUERY_KEY }),
    [queryClient],
  );
}
