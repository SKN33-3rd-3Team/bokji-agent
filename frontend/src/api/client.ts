import axios, { type AxiosError } from "axios";
import type { ApiErrorBody } from "@/types/auth";

/**
 * 공용 axios 인스턴스. PROJECT_STRUCTURE.md 3.1 트리의 api/client.ts 명세
 * 그대로: withCredentials:true(세션 쿠키 포함), baseURL은 VITE_API_BASE_URL.
 * 백엔드(backend/)는 이번 작업 범위가 아니라 아직 없다 — 호출 자체는
 * API_정의서.xlsx 계약대로 실제 엔드포인트를 부르도록 작성하고, 실행 시에는
 * 네트워크 에러/로딩 상태로만 나타난다.
 */
export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000",
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

/** 서버 에러 응답을 ApiError로 통일해서 던진다. */
export class ApiError extends Error {
  readonly status: number;
  readonly errorCode: string;
  readonly remainingSeconds?: number;
  readonly violations?: string[];

  constructor(status: number, body: Partial<ApiErrorBody>) {
    super(body.message ?? "요청 처리 중 오류가 발생했습니다.");
    this.name = "ApiError";
    this.status = status;
    this.errorCode = body.error_code ?? "UNKNOWN_ERROR";
    this.remainingSeconds = body.remaining_seconds;
    this.violations = body.violations;
  }
}

/**
 * 화면에 그대로 보여줄 에러 문구로 변환한다. apiClient를 거친 에러는
 * 인터셉터가 항상 ApiError로 감싸 던지지만(네트워크 단절도 포함), 그 경로를
 * 거치지 않은 예외가 섞여 들어올 가능성까지 방어적으로 처리한다.
 */
export function toErrorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "일시적인 오류가 발생했습니다. 다시 시도해주세요.";
}

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<Partial<ApiErrorBody>>) => {
    if (error.response) {
      throw new ApiError(error.response.status, error.response.data ?? {});
    }
    // 네트워크 자체가 끊긴 경우(백엔드 미기동 등) — 화면은 이걸 "일시적인
    // 오류" 배너로 보여주면 된다.
    throw new ApiError(0, { message: "서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요." });
  },
);
