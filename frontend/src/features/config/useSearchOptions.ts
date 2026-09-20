import { useQuery } from "@tanstack/react-query";
import { getSearchOptions } from "@/api/configApi";

/** API-09 — 정적 데이터라 staleTime을 길게 잡아 재요청을 최소화한다. */
export function useSearchOptions() {
  return useQuery({
    queryKey: ["search-options"],
    queryFn: getSearchOptions,
    staleTime: 30 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
  });
}
