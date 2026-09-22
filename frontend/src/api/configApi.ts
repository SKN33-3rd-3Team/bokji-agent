import { apiClient } from "./client";
import type { SearchOptions } from "@/types/auth";
import type { ServerStatus } from "@/types/chat";

/** API-09 GET /api/v1/config/search-options — 앱 시작 시 1회 조회 후 캐싱 권장 */
export async function getSearchOptions(): Promise<SearchOptions> {
  const { data } = await apiClient.get<SearchOptions>("/api/v1/config/search-options");
  return data;
}

/**
 * GET /api/v1/config/status — 서버 구동 워밍업(임베딩 모델 로드) 상태.
 * 백엔드가 워밍업을 별도 스레드에서 돌리기 때문에(backend/app/main.py
 * lifespan), 그 사이에 시작한 첫 상담만 유난히 느리다. 화면이 "검색 엔진
 * 준비 중"을 미리 알려줄 수 있게 이 값을 읽는다.
 */
export async function getServerStatus(): Promise<ServerStatus> {
  const { data } = await apiClient.get<ServerStatus>("/api/v1/config/status");
  return data;
}
