"""추론형 모델의 '사고 끄기'를 어떤 방법으로 걸어야 하는지 실측으로 고른다.

배경(2026-09-14): Qwen3.5-9B를 쓰는 이 파이프라인은 두 상태 모두 못 쓴다.

  LLM_DISABLE_THINKING=1 -> build_llm_client()가
      extra_body={"chat_template_kwargs": {"enable_thinking": False}}를 붙인다.
      HF provider 라우팅이 이걸 거부해 **모든 호출이 HTTP 400**이 된다
      (원문은 마커 없는 "Bad request:"). 노드는 규칙 기반으로 조용히 폴백하므로
      100문항이 정상 완료되고 지표까지 찍히지만 전부 폴백 성능이다.
  LLM_DISABLE_THINKING=0 -> extra_body 없음. 호출은 되지만 사고 토큰을 그대로
      써서 질문 하나에 수 분이 걸린다(실측: 15분에 2문항).

즉 "사고를 끄되 provider가 받아주는 경로"를 찾아야 한다. 이 스크립트는 후보를
같은 프롬프트·같은 토큰 예산으로 한 번씩 때려보고 성공 여부와 걸린 시간을
나란히 보여준다. 답은 모델·provider·시점에 따라 달라지므로 추측하지 않고 잰다.

후보
----
1. baseline            - 아무것도 안 함(현재 =0 상태). 느린 기준선
2. extra_body(nested)  - 현재 =1이 보내는 것. 400 재현 확인용
3. extra_body(flat)    - {"enable_thinking": False} 를 최상위로
4. /no_think (user)    - Qwen 계열 chat template의 프롬프트 소프트 스위치
5. /no_think (system)  - 같은 스위치를 system 메시지에

실행:
    python scripts/probe_thinking_switch.py
    python scripts/probe_thinking_switch.py --max-tokens 2048 --repeat 2
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

from src.rag_chatbot.llm.client import (  # noqa: E402
    HuggingFaceInferenceClient,
    LLMCallError,
)

# 실제 노드들이 하는 일과 비슷한 난이도로 잡는다. 너무 쉬우면(예: {"ok": true})
# 모델이 사고를 거의 안 해서 변형 간 차이가 안 드러난다 - check_llm_connection.py가
# 1024로도 성공했는데 실제 실행은 느렸던 이유가 이것이다.
_SYSTEM = "당신은 복지정책 문서를 읽고 JSON 객체 하나만 출력하는 도구입니다."
_PROMPT = """아래 정책 설명을 읽고 지원 대상과 지원 금액을 뽑아 JSON으로만 답하세요.

[정책]
근로장려금은 일은 하지만 소득이 적어 생활이 어려운 근로자·사업자(전문직 제외)
가구에 대해 가구원 구성과 총급여액 등에 따라 산정된 근로장려금을 지급하여
근로를 장려하고 실질소득을 지원하는 제도입니다. 단독가구는 총급여액 2,200만원
미만, 홑벌이가구는 3,200만원 미만, 맞벌이가구는 4,400만원 미만이어야 하며
가구원 재산 합계액이 2억 4천만원 미만이어야 합니다.

[출력 형식]
{"지원대상": "...", "소득요건": {"단독가구": "...", "홑벌이가구": "...", "맞벌이가구": "..."}, "재산요건": "..."}"""


def _run_once(
    label: str,
    *,
    model: str,
    token: str | None,
    max_tokens: int,
    timeout: float,
    extra_body: dict | None,
    prompt_suffix: str,
    system_suffix: str,
) -> dict:
    client = HuggingFaceInferenceClient(
        model=model,
        token=token,
        max_new_tokens=max_tokens,
        timeout_seconds=timeout,
        extra_body=extra_body,
    )
    started = time.monotonic()
    try:
        text = client.complete(_PROMPT + prompt_suffix, system=_SYSTEM + system_suffix)
    except LLMCallError as exc:
        return {
            "label": label,
            "ok": False,
            "seconds": time.monotonic() - started,
            "detail": str(exc).split(" / ")[0][:150],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "label": label,
            "ok": False,
            "seconds": time.monotonic() - started,
            "detail": f"{type(exc).__name__}: {exc}"[:150],
        }
    return {
        "label": label,
        "ok": True,
        "seconds": time.monotonic() - started,
        "detail": " ".join(text.split())[:90],
    }


_THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


def _provider_scan(args, *, token: str) -> int:
    """provider를 고정해가며 extra_body(사고 끄기)가 통하는 곳을 찾는다.

    판정 기준 세 가지:
      OK       - 호출 성공. 사고가 실제로 꺼졌다면 짧은 예산으로도 답이 끝난다.
      잘림      - provider가 extra_body를 조용히 무시했다는 뜻이다(사고가 켜진 채
                 토큰을 다 씀). 400은 아니지만 쓸 수 없는 건 마찬가지다.
      400/에러  - 그 provider가 계정에 없거나 extra_body를 거부한다. 원문으로 구분.
    """

    providers = [p.strip() for p in args.provider_list.split(",") if p.strip()]
    print("=" * 74)
    print(f"provider별 extra_body(사고 끄기) 지원 실측 - 모델 {args.model}")
    print(f"max_tokens={args.max_tokens} (사고가 꺼지면 이 예산으로 충분해야 한다)")
    print("=" * 74)

    rows: list[tuple[str, dict]] = []
    for provider in providers:
        print(f"\n[{provider}] 호출 중...", flush=True)
        client_kwargs = dict(
            model=args.model,
            token=token,
            max_new_tokens=args.max_tokens,
            timeout_seconds=args.timeout,
            extra_body=_THINKING_OFF,
            provider=provider,
        )
        started = time.monotonic()
        try:
            client = HuggingFaceInferenceClient(**client_kwargs)
            text = client.complete(_PROMPT, system=_SYSTEM)
            result = {
                "ok": True,
                "seconds": time.monotonic() - started,
                "detail": " ".join(text.split())[:80],
            }
        except LLMCallError as exc:
            message = str(exc)
            result = {
                "ok": False,
                "truncated": "잘림" in message,
                "seconds": time.monotonic() - started,
                "detail": message.split(" / ")[0][:120],
            }
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "truncated": False,
                "seconds": time.monotonic() - started,
                "detail": f"{type(exc).__name__}: {exc}"[:120],
            }
        rows.append((provider, result))
        if result["ok"]:
            mark = "OK  "
        elif result.get("truncated"):
            mark = "무시"
        else:
            mark = "FAIL"
        print(f"  {mark} {result['seconds']:6.1f}s  {result['detail']}")

    print("\n" + "=" * 74)
    print("요약")
    print("=" * 74)
    winners = [(p, r) for p, r in rows if r["ok"]]
    ignored = [(p, r) for p, r in rows if not r["ok"] and r.get("truncated")]
    for provider, result in rows:
        if result["ok"]:
            mark = "OK  "
        elif result.get("truncated"):
            mark = "무시"
        else:
            mark = "FAIL"
        print(f"  {mark} {result['seconds']:6.1f}s  {provider}")

    if winners:
        best = min(winners, key=lambda pr: pr[1]["seconds"])
        print(f"\n사고 끄기가 통하는 provider: {[p for p, _ in winners]}")
        print(f"가장 빠른 곳: {best[0]} ({best[1]['seconds']:.1f}s)")
        print(
            "build_llm_client()가 provider를 고정하도록 배선하면 라우팅이 흔들려도"
            " 안 깨집니다."
        )
        return 0
    if ignored:
        print(f"\nextra_body를 조용히 무시하는 provider: {[p for p, _ in ignored]}")
        print("호출은 되지만 사고가 켜진 채라 예산 안에 답을 못 씁니다.")
    print("\n사고를 끌 수 있는 provider가 없습니다 - 비추론형 모델이나 직접 서빙")
    print("(RunPod+vLLM 등, chat template을 우리가 통제)을 검토하세요.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=os.environ.get("LLM_MODEL_NAME") or os.environ.get("LLM_HF_MODEL"),
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.environ.get("LLM_MAX_NEW_TOKENS") or 1024),
        help=".env의 LLM_MAX_NEW_TOKENS와 같은 값으로 재야 의미가 있다.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="사고를 켜면 기본 60초로는 타임아웃이 난다. 넉넉히 잡는다.",
    )
    parser.add_argument("--repeat", type=int, default=1, help="변형마다 반복 횟수")
    parser.add_argument(
        "--only",
        help="쉼표로 구분한 변형 번호만 실행(예: --only 1). 토큰 예산을 올려가며"
        " baseline만 다시 잴 때 쓴다 - 나머지를 매번 돌릴 이유가 없다.",
    )
    parser.add_argument(
        "--provider-scan",
        action="store_true",
        help="provider를 하나씩 고정해 extra_body가 먹히는 곳을 찾는다. HF는 같은"
        " 모델을 여러 provider로 동적 라우팅하고 chat_template_kwargs 지원 여부가"
        " provider마다 달라서, 코드를 안 고쳐도 어제 되던 게 오늘 400이 난다.",
    )
    parser.add_argument(
        "--provider-list",
        default="together,fireworks-ai,nebius,hyperbolic,novita,sambanova,"
        "cerebras,groq,featherless-ai,nscale,hf-inference",
        help="스캔할 provider 이름(쉼표 구분). HF가 provider를 추가/제거하면 바꿔서 쓴다.",
    )
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        print("HF_TOKEN이 없습니다 (.env 확인).", file=sys.stderr)
        return 1
    if not args.model:
        print("LLM_MODEL_NAME이 없습니다 (.env 확인).", file=sys.stderr)
        return 1

    if args.provider_scan:
        return _provider_scan(args, token=token)

    variants = [
        ("1. baseline (사고 켬)", None, "", ""),
        (
            "2. extra_body nested",
            {"chat_template_kwargs": {"enable_thinking": False}},
            "",
            "",
        ),
        ("3. extra_body flat", {"enable_thinking": False}, "", ""),
        ("4. /no_think (user)", None, "\n\n/no_think", ""),
        ("5. /no_think (system)", None, "", " /no_think"),
    ]

    if args.only:
        wanted = {part.strip() for part in args.only.split(",") if part.strip()}
        variants = [v for v in variants if v[0].split(".")[0] in wanted]
        if not variants:
            print(f"--only {args.only!r}에 해당하는 변형이 없습니다.", file=sys.stderr)
            return 1

    print("=" * 74)
    print(f"사고 끄기 경로 실측 - 모델 {args.model}, max_tokens={args.max_tokens}")
    print("=" * 74)

    results: list[dict] = []
    for label, extra_body, prompt_suffix, system_suffix in variants:
        for attempt in range(args.repeat):
            shown = label if args.repeat == 1 else f"{label} #{attempt + 1}"
            print(f"\n[{shown}] 호출 중...", flush=True)
            result = _run_once(
                shown,
                model=args.model,
                token=token,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
                extra_body=extra_body,
                prompt_suffix=prompt_suffix,
                system_suffix=system_suffix,
            )
            results.append(result)
            mark = "OK  " if result["ok"] else "FAIL"
            print(f"  {mark} {result['seconds']:6.1f}s  {result['detail']}")

    print("\n" + "=" * 74)
    print("요약 (성공한 것 중 가장 빠른 게 정답)")
    print("=" * 74)
    for result in sorted(results, key=lambda r: (not r["ok"], r["seconds"])):
        mark = "OK  " if result["ok"] else "FAIL"
        print(f"  {mark} {result['seconds']:6.1f}s  {result['label']}")

    working = [r for r in results if r["ok"]]
    if not working:
        print("\n전부 실패 - 모델/토큰 쪽 문제일 수 있습니다.")
        return 1
    baseline = next((r for r in results if r["label"].startswith("1.") and r["ok"]), None)
    best = min(working, key=lambda r: r["seconds"])
    print(f"\n가장 빠른 성공: {best['label']} ({best['seconds']:.1f}s)")
    if baseline and best is not baseline:
        saved = baseline["seconds"] - best["seconds"]
        print(
            f"baseline 대비 호출당 {saved:.1f}s 단축"
            f" ({saved / baseline['seconds']:.0%})."
        )
        print("이 경로를 build_llm_client()에 적용하면 됩니다.")
    else:
        print("사고를 끄는 경로가 baseline보다 빠르지 않습니다 -")
        print("모델을 비추론형으로 바꾸는 쪽을 검토하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
