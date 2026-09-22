import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getServerStatus } from "@/api/configApi";
import { useChatProgress } from "@/features/chat/useChatProgress";
import { PROGRESS_FALLBACK_MESSAGE, PROGRESS_WARMUP_MESSAGE } from "@/constants/labels";

interface ChatProgressBarProps {
  /** useChatSession이 요청마다 새로 만드는 진행률 조회 토큰 */
  token: string | null;
  /** 요청이 도는 동안만 true */
  active: boolean;
  startedAt?: number | null;
}

/**
 * S03-06 확장: 상담이 도는 동안 "지금 무슨 일을 하고 있는지"를 화면에 그린다.
 *
 * 이 단계 정보는 원래 서버 콘솔에만 찍혔다(BOKJI_TRACE 로그). 한 번 물어보면
 * 수십 초에서 수 분까지 걸리는데 화면에는 점 세 개(TypingIndicator)만 떠 있어,
 * 사용자 입장에서는 멈춘 건지 도는 건지 구분할 수 없었다.
 *
 * 진행률은 **어림값**이다 — 그래프가 조건부 분기를 타서 전체 단계 수는
 * 끝나봐야 알기 때문에, 백엔드가 끝나기 전에는 95%를 넘기지 않는다. 그래서
 * 숫자(%)는 굳이 크게 쓰지 않고 막대와 단계 문구, 경과 시간만 보여준다.
 */
export function ChatProgressBar({ token, active, startedAt }: ChatProgressBarProps) {
  const progress = useChatProgress(token, active);
  const [, refresh] = useState(0);
  useEffect(() => {
    if (!active || !startedAt) return;
    const timer = window.setInterval(() => refresh((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active, startedAt]);

  // 서버 구동 워밍업(임베딩 모델 로드)이 아직 안 끝났으면 첫 상담만 유난히
  // 느리다. 그 사실을 알려주지 않으면 "왜 이렇게 느리지"로만 남는다.
  const { data: serverStatus } = useQuery({
    queryKey: ["server-status"],
    queryFn: getServerStatus,
    enabled: active,
    refetchInterval: 5000,
    refetchOnWindowFocus: false,
    retry: false,
  });

  if (!active) return null;

  const warmingUp =
    serverStatus?.status === "pending" || serverStatus?.status === "running";
  const fraction = progress?.fraction ?? 0;
  const clientElapsed = startedAt ? Math.max(0, (Date.now() - startedAt) / 1000) : 0;
  const elapsed = Math.max(progress?.elapsed_seconds ?? 0, clientElapsed);
  const message =
    warmingUp && (progress?.completed_steps ?? 0) === 0
      ? PROGRESS_WARMUP_MESSAGE
      : progress?.message ?? PROGRESS_FALLBACK_MESSAGE;

  return (
    <div
      className="card progress-card"
      role="status"
      aria-live="polite"
      aria-busy="true"
      style={{ marginBottom: 16 }}
    >
      <div className="progress-head">
        <span className="progress-message">{message}</span>
        <span className="progress-elapsed">{Math.round(elapsed)}초</span>
      </div>

      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(fraction * 100)}
        aria-label="상담 진행률"
      >
        <div className="progress-fill" style={{ width: `${Math.max(fraction * 100, 3)}%` }} />
      </div>

      <p className="progress-note text-faint">
        {progress && progress.completed_steps > 0
          ? `${progress.completed_steps}/${progress.total_steps}단계 진행 중 · 근거를 확인하느라 조금 걸려요`
          : "근거를 확인하느라 조금 걸려요. 창을 닫지 말고 기다려 주세요."}
      </p>
    </div>
  );
}
