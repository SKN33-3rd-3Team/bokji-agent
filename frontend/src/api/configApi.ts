import { apiClient } from "./client";
import type { SearchOptions } from "@/types/auth";

/** API-09 GET /api/v1/config/search-options — 앱 시작 시 1회 조회 후 캐싱 권장 */
export async function getSearchOptions(): Promise<SearchOptions> {
  const { data } = await apiClient.get<SearchOptions>("/api/v1/config/search-options");
  return data;
}
