"""HuggingFace LLM 연결이 되는지, 안 되면 **왜** 안 되는지 한 번에 확인한다.

실행:
    pip install -r requirements-graph.txt
    python scripts/check_llm_connection.py
    python scripts/check_llm_connection.py --model Qwen/Qwen3.5-9B

왜 필요한가: N1/N5/N9/N10/N13은 LLM 호출이 실패해도 규칙 기반으로 폴백해서
그래프를 끝까지 돌린다(의도된 설계). 그래서 **LLM이 한 번도 안 돌았는데도
답변은 멀쩡히 나온다.** 실제로 HF 토큰이 403을 뱉는 동안에도 챗봇은 정상
동작하는 것처럼 보였다. 이 스크립트는 그 폴백을 거치지 않고 LLM만 직접
때려보고, 실패하면 무엇을 고쳐야 하는지까지 출력한다.

확인 순서
---------
1. .env / 환경변수에 HF_TOKEN이 있는지
2. huggingface_hub 설치 여부
3. 토큰 자체가 유효한지 (whoami - Inference 호출 권한과는 별개)
4. 실제 chat_completion 호출이 되는지 (크레딧/권한/provider 문제가 여기서 드러남)
5. 실패하면 HTTP 상태코드별 조치 안내
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

from src.rag_chatbot.llm import LLMCallError, diagnose_hf_error  # noqa: E402
from src.rag_chatbot.service import _DEFAULT_HF_MODEL  # noqa: E402

_OK = "[OK]"
_FAIL = "[실패]"
_INFO = "[안내]"

_SETTINGS_TOKENS = "https://huggingface.co/settings/tokens"
_SETTINGS_BILLING = "https://huggingface.co/settings/billing"
_SETTINGS_PROVIDERS = "https://huggingface.co/settings/inference-providers"


def _mask(token: str) -> str:
    """토큰을 로그에 그대로 남기지 않는다."""

    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]} (길이 {len(token)})"


def _check_token() -> str | None:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        print(f"{_FAIL} HF_TOKEN이 없습니다.")
        print(f"       -> {_SETTINGS_TOKENS} 에서 토큰을 만들고 .env에 HF_TOKEN=... 으로 넣으세요.")
        print("       -> 토큰 없이도 챗봇은 돌아가지만, 전부 규칙 기반으로만 동작합니다.")
        return None
    print(f"{_OK} HF_TOKEN 발견: {_mask(token)}")
    if not token.startswith("hf_"):
        print("       [경고] 보통 HF 토큰은 'hf_'로 시작합니다. 값을 잘못 붙여넣지 않았는지 확인하세요.")
    return token


def _check_library() -> bool:
    try:
        import huggingface_hub
    except ImportError:
        print(f"{_FAIL} huggingface_hub가 설치되어 있지 않습니다.")
        print("       -> pip install -r requirements-graph.txt")
        return False
    print(f"{_OK} huggingface_hub 설치됨 (버전 {huggingface_hub.__version__})")
    return True


def _check_whoami(token: str) -> bool:
    """토큰이 유효한 계정 토큰인지 본다.

    이 단계를 따로 두는 이유가 중요하다. ``whoami``는 **계정 정보를 읽기만**
    하는 호출이라 크레딧도 provider 활성화도 필요 없다. 그래서 여기서
    실패하면 원인이 크게 좁혀진다:

    - whoami 실패 + 호출 실패 -> **토큰 자체의 문제**(권한/만료). 크레딧이나
      provider 설정을 아무리 만져도 해결되지 않는다.
    - whoami 성공 + 호출 실패 -> 토큰은 살아 있고, Inference 쪽 문제
      (호출 권한 미포함 / provider 미활성 / 크레딧).

    반대로 여기서 성공해도 Inference 호출은 실패할 수 있다 - 두 권한이
    별개이기 때문이다.
    """

    from huggingface_hub import HfApi

    try:
        info = HfApi(token=token).whoami()
    except Exception as exc:  # noqa: BLE001 - 원인을 그대로 보여주는 게 목적
        print(f"{_FAIL} 토큰으로 계정 조회(whoami) 실패: {exc}")
        print()
        detail = str(exc).lower()
        if any(m in detail for m in ("proxy", "tunnel", "connection", "dns", "timed out")):
            # 사내망/샌드박스가 huggingface.co를 막으면 프록시가 403 같은 코드를
            # 달고 온다. 이걸 토큰 문제로 읽으면 엉뚱한 곳을 고치게 된다
            # (2026-08-31에 실제로 이 오진을 했다).
            print("       네트워크/프록시가 huggingface.co 연결을 막고 있습니다.")
            print("       **토큰 문제가 아닙니다** - 요청이 HuggingFace에 닿지도 못했습니다.")
            print("       외부 접속이 제한된 환경(사내망/샌드박스)인지 확인하세요.")
            print()
            return False
        print("       whoami는 계정 정보를 읽는 호출입니다. 다만 fine-grained")
        print("       토큰은 사용자 정보 읽기 권한이 빠져 있으면 여기서 정상적으로")
        print("       거부될 수 있으니, 이 실패만으로 토큰이 죽었다고 단정하지")
        print("       마세요 - 아래 실제 호출 결과를 함께 보고 판단하면 됩니다.")
        print()
        print(f"       조치: {_SETTINGS_TOKENS} 에서")
        print("         (a) 이 토큰이 아직 살아 있는지(삭제/만료되지 않았는지) 확인하고,")
        print("         (b) 없거나 의심스러우면 새 토큰을 만드세요. HuggingFace 공식")
        print("             안내는 Fine-grained 토큰 + 'Make calls to Inference Providers'")
        print("             권한입니다. 아래 링크로 열면 그 권한이 미리 체크됩니다:")
        print("             https://huggingface.co/settings/tokens/new"
              "?ownUserPermissions=inference.serverless.write&tokenType=fineGrained")
        print("             Repositories의 'Read contents of your repos'도 함께 체크하세요.")
        print("         (c) 새 토큰을 .env의 HF_TOKEN= 에 넣고 다시 실행하세요.")
        return False

    print(f"{_OK} 토큰 유효 - 계정: {info.get('name')} (타입 {info.get('type')})")
    auth = (info.get("auth") or {}).get("accessToken") or {}
    if auth:
        print(f"       토큰 이름: {auth.get('displayName')} / 권한 role: {auth.get('role')}")
    orgs = [org.get("name") for org in info.get("orgs", [])]
    if orgs:
        # 결제를 조직 계정에 했는데 토큰은 개인 계정인 경우가 흔하다.
        print(f"       소속 조직: {', '.join(str(o) for o in orgs)}")
        print("       [확인] 결제를 조직 계정에 하셨다면, 그 조직 소속 토큰인지 확인하세요.")
    return True


def _check_inference(token: str, model: str, max_tokens: int) -> bool:
    """실제로 한 번 호출해본다. 크레딧/권한/provider 문제는 여기서만 드러난다."""

    from src.rag_chatbot.llm import HuggingFaceInferenceClient

    print(f"\n{_INFO} 실제 호출 테스트 - 모델 {model!r}, max_new_tokens={max_tokens}")
    client = HuggingFaceInferenceClient(model=model, token=token, max_new_tokens=max_tokens)
    prompt = '아래 JSON 형식으로만 답하세요. {"ok": true}'
    try:
        response = client.complete(prompt, system="당신은 JSON만 출력하는 도구입니다.")
    except LLMCallError as exc:
        print(f"{_FAIL} 호출 실패\n")
        # diagnose_hf_error가 이미 조치 안내까지 담아준다.
        for line in str(exc).split(" / "):
            print(f"       {line}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"{_FAIL} 예상치 못한 예외: {diagnose_hf_error(exc, model)}")
        return False

    preview = response.strip().replace("\n", " ")[:200]
    print(f"{_OK} 호출 성공! 응답 미리보기: {preview!r}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="HuggingFace LLM 연결 진단")
    parser.add_argument(
        "--model",
        default=os.environ.get("LLM_MODEL_NAME")
        or os.environ.get("LLM_HF_MODEL")
        or _DEFAULT_HF_MODEL,
        help="테스트할 모델. 기본값은 .env의 LLM_MODEL_NAME(없으면 프로젝트 기본 모델).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=256,
        help="연결 확인용이라 작게 잡는다(기본 256). 추론형 모델은 이 값이 작으면 "
        "빈 응답이 날 수 있는데, 그건 연결 문제가 아니라 토큰 예산 문제다.",
    )
    args = parser.parse_args()

    print("=" * 68)
    print("HuggingFace LLM 연결 진단")
    print("=" * 68)

    token = _check_token()
    if token is None:
        return 1
    if not _check_library():
        return 1
    token_alive = _check_whoami(token)

    if _check_inference(token, args.model, args.max_tokens):
        print("\n결론: LLM 연결 정상. N1/N5/N9/N10/N13이 실제 LLM으로 동작합니다.")
        return 0

    print("\n결론: LLM 연결 실패. 챗봇은 계속 동작하지만 **전부 규칙 기반**입니다.")
    if not token_alive:
        print()
        print("참고: 계정 조회(whoami)도 실패했습니다. 위 실패 사유가 네트워크/")
        print("프록시라면 토큰과 무관하고, 그렇지 않다면 토큰 유효성도 함께")
        print("확인해보세요(fine-grained 토큰은 정상이어도 whoami가 막힐 수 있습니다).")
        print()
    print("\n자주 겪는 원인 세 가지:")
    print(f"  1. 토큰 권한       - {_SETTINGS_TOKENS}")
    print("     Fine-grained 토큰에 'Make calls to Inference Providers'가 켜져 있어야")
    print("     합니다. 이 권한 없이는 모델/크레딧과 무관하게 전부 거부됩니다.")
    print(f"  2. provider 미활성 - {_SETTINGS_PROVIDERS}")
    print("     모델 페이지의 'Inference Providers'에 적힌 provider를 켜야 합니다")
    print("     (예: Bllossom / A.X-4.0-Light는 Featherless AI).")
    print(f"  3. 크레딧          - {_SETTINGS_BILLING}")
    print("     무료 티어는 월 $0.10입니다. 결제했는데도 402면 반영 지연이거나")
    print("     결제한 계정과 토큰 계정이 다를 수 있습니다.")
    print("\n다른 모델로도 확인해보세요:")
    print("  python scripts/check_llm_connection.py --model Qwen/Qwen3.5-9B")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
