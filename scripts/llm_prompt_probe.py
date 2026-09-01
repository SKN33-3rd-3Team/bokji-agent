"""N5/N9/N10/N13이 실제로 보내는 프롬프트를, fine-tuning 없이 후보
instruct 모델(HuggingFace Inference API)에 그대로 넣어서 "프롬프팅만으로
되는지" 확인하는 스크립트.

**이 스크립트는 실제 네트워크가 되는 이 컴퓨터의 터미널에서 직접 실행해야
한다.** Claude가 접근할 수 있는 클라우드 작업공간과, 이 컴퓨터에 연결된
로컬 VM 샌드박스 둘 다 huggingface.co로 나가는 경로가 막혀 있어서(egress
allowlist), 여기서는 이 스크립트를 대신 돌려볼 수 없었다 - 그래서 직접
실행해서 결과를 보셔야 한다.

각 노드가 실제로 쓰는 프롬프트/파싱/검증 로직을 그대로 재사용한다
(``LLMClaimExtractor``, ``_naturalize_reasons``, ``_extract_amount_via_llm``,
``generate_answer``를 직접 import) - 이 스크립트가 만든 별도 프롬프트가
아니라, 그래프에 실제로 붙였을 때 나갈 프롬프트 그대로를 테스트한다. 그래서
"규칙 기반으로 폴백됐는지" 여부가 곧 "이 모델이 실제로 이 파이프라인에서
LLM으로 통하는지"의 답이다.

사용법 (레포 루트에서, PowerShell):
    pip install -r requirements-graph.txt
    $env:PYTHONPATH = ".;src"
    python scripts/llm_prompt_probe.py

HF_TOKEN은 매번 셸에 치는 대신 레포 루트의 ``.env`` 파일에 넣어두면
자동으로 읽힌다(``.env.example`` 참고, ``.gitignore``에 이미 있어 커밋되지
않음):

    # .env
    HF_TOKEN=hf_...

판단 기준
---------
- N5: ``claims``가 ``RuleBasedClaimExtractor``와 같은 결과면(=텍스트 전체를
  reasons로 쓴 3개짜리 claim) LLM 출력이 JSON 스키마나 "원문 그대로 인용"
  검증을 통과하지 못해 폴백된 것이다. 다른 결과가 나오면 LLM이 실제로
  claim_type별로 원문에서 근거 문장을 뽑아낸 것이다.
- N9/N13: 원문 사실(숫자·조건·제도명)이 그대로 보존됐는지, 문장만 자연스러워
  졌는지 눈으로 확인한다. 숫자가 하나라도 바뀌었으면(예: "월 20만원"이
  "월 25만원"으로) 그 모델은 이 프롬프트로는 못 믿는다는 뜻이다.
- N10: ``amount``가 원문에 있는 200000을 정확히 뽑았는지, 아니면 조건부
  상황을 만나 null로 정직하게 포기했는지 확인한다.

이 네 가지가 전부 원문 사실을 안 바꾸고 형식만 지키면, 이 네 노드는
fine-tuning 없이 프롬프팅만으로 될 가능성이 높다는 뜻이다(정확도를 수치로
보장하는 건 아니고, "이 모델이 이 정도 지시를 따라올 수 있는 능력이 있다"는
정성적 신호). 반대로 여러 모델에서 계속 규칙 기반으로 폴백되거나, 숫자/
조건이 바뀌어 나오면 fine-tuning(또는 few-shot 예시 추가, 더 큰 모델)이
필요하다는 신호다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()  # 레포 루트 .env의 HF_TOKEN을 os.environ으로 읽어들인다.

from rag_design.contracts import (
    Chunk,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)
from src.rag_chatbot.graph.nodes.answer_generation import generate_answer
from src.rag_chatbot.graph.nodes.benefit_calculator import _extract_amount_via_llm
from src.rag_chatbot.graph.nodes.claim_extractor import (
    LLMClaimExtractor,
    RuleBasedClaimExtractor,
)
from src.rag_chatbot.graph.nodes.eligibility_verdict import _naturalize_reasons
from src.rag_chatbot.llm import HuggingFaceInferenceClient, LLMCallError

# client.py 상단 docstring에 적힌 세 후보. Qwen 쪽은 정확한 HF repo 이름/
# instruct 여부를 이 스크립트 작성 시점에 직접 확인하지 못했다(네트워크
# 제한) - 안 되면 목록에서 빼거나 정확한 이름으로 고쳐서 다시 돌리면 된다.
CANDIDATE_MODELS = [
    "Bllossom/llama-3.2-Korean-Bllossom-3B",
    "skt/A.X-4.0-Light",
    "Qwen/Qwen3.5-9B",  # 확인 필요: instruct 버전 repo 이름이 다를 수 있음
]

# N5/N10 공통 샘플: 자격 조건 + 확정 금액이 둘 다 명시된 정책 원문.
SAMPLE_POLICY_TEXT = (
    "지원대상\n"
    "만 65세 이상 저소득 어르신 중 소득인정액이 기준 중위소득 50% 이하인 자.\n"
    "지원내용\n"
    "1인당 월 200,000원을 매월 20일 지급한다. 타 유사 현금성 지원과 "
    "중복수급은 불가하다."
)

# N9 샘플: 규칙이 기계적으로 만든 위반 사유 (숫자/조건이 그대로 보존돼야 함).
SAMPLE_RULE_REASONS = [
    "연령 조건 미충족: 최소 65세부터 지원 (사용자 age=40)",
    "소득 조건 미충족: 기준 중위소득 50% 이하만 지원 (사용자 income_bracket=pct_100_150)",
]


def _sample_chunk(chunk_id: str, source_url: str) -> Chunk:
    return Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=chunk_id,
        doc_id="probe-policy",
        source_type=SourceType.SUBSIDY,
        text=SAMPLE_POLICY_TEXT,
        heading_path=("지원대상",),
        ordinal=0,
        citation_locator="지원대상",
        content_hash=compute_content_hash(SAMPLE_POLICY_TEXT),
        metadata={"source_url": source_url},
    )


def _sample_state_for_n13() -> dict:
    chunk = _sample_chunk("probe-policy-chunk-1", "https://www.gov.kr/portal/rcvfvrSvc/dtlEx/probe")
    retrieved = RetrievedChunk(
        query_id="probe",
        chunk=chunk,
        rank=1,
        score=0.1,
        score_type="cosine_distance",
        retriever_version="probe:fixture",
        index_name="subsidy",
    )
    return {
        "assembled_result": {
            "policies": {
                "probe-policy": {
                    "eligibility": {
                        "policy_id": "probe-policy",
                        "verdict": "충족",
                        "reasons": ["근거 문장"],
                    },
                    "benefit_amount": {"policy_id": "probe-policy", "amount": 200000.0},
                    "duplicate": {"policy_id": "probe-policy", "status": "미확인"},
                }
            }
        },
        "claim_plan": [
            {
                "claim_id": "c1",
                "policy_id": "probe-policy",
                "claim_type": "eligibility",
                "evidence_chunk_ids": ["probe-policy-chunk-1"],
            }
        ],
        "subsidy_chunks": [retrieved],
        "law_chunks": [],
        "node_trace": [],
    }


def _print_header(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


def _print_sub(text: str) -> None:
    print(f"\n--- {text} ---")


class _LoggingLLMClient:
    """실제 클라이언트를 감싸서 마지막 raw 요청/응답/예외를 기록해두는 진단용
    래퍼. N5(claim_extractor)/N13(answer_generation)은 LLM 호출이 실패하거나
    검증을 통과 못하면 "조용히" 규칙 기반/템플릿으로 폴백하도록 설계돼 있어서
    (그래프가 죽으면 안 되니까), 바깥에서 보면 "폴백됐다"는 사실만 보이고
    원인(호출 자체가 실패했는지, 응답은 왔는데 파싱/검증에서 걸렸는지)은 안
    보인다. 이 래퍼는 그 원인을 이 스크립트가 직접 들여다볼 수 있게 한다 -
    프로덕션 코드(client.py)는 건드리지 않는다.
    """

    def __init__(self, inner):
        self.inner = inner
        self.last_response: str | None = None
        self.last_error: BaseException | None = None

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.last_response = None
        self.last_error = None
        try:
            response = self.inner.complete(prompt, system=system)
            self.last_response = response
            return response
        except Exception as exc:  # noqa: BLE001 - 무엇이든 그대로 기록하고 다시 던짐
            self.last_error = exc
            raise

    def _print_diagnosis(self, *, fell_back: bool) -> None:
        if not fell_back:
            return
        if self.last_error is not None:
            print(f"  [진단] LLM 호출 자체가 실패함: {self.last_error!r}")
        else:
            print(f"  [진단] LLM 원문 응답(파싱/검증 전, 이래서 폴백됐는지 확인용):\n    {self.last_response!r}")


def probe_model(model_name: str) -> None:
    _print_header(f"모델: {model_name}")
    try:
        client = HuggingFaceInferenceClient(model=model_name)
    except ValueError as exc:
        print(f"  [건너뜀] {exc}")
        return
    logged = _LoggingLLMClient(client)

    _print_sub("N5 claim_plan (claim 추출 JSON)")
    try:
        claims = LLMClaimExtractor(logged).extract(policy_id="probe-policy", text=SAMPLE_POLICY_TEXT)
        fell_back = claims == RuleBasedClaimExtractor().extract(
            policy_id="probe-policy", text=SAMPLE_POLICY_TEXT
        )
        print(f"  결과: {claims}")
        print(f"  규칙 기반으로 폴백됨? {fell_back}  (True면 LLM 출력이 검증을 통과 못함)")
        logged._print_diagnosis(fell_back=fell_back)
    except Exception as exc:  # noqa: BLE001 - 진단 스크립트, 모델별로 어떤 예외든 계속 진행
        print(f"  [예외] {exc!r}")

    _print_sub("N9 eligibility_verdict (위반 사유 자연어화)")
    try:
        naturalized = _naturalize_reasons(SAMPLE_RULE_REASONS, logged)
        fell_back = naturalized == SAMPLE_RULE_REASONS
        print(f"  원문: {SAMPLE_RULE_REASONS}")
        print(f"  결과: {naturalized}")
        print(f"  원문 그대로임(=LLM 응답을 못 씀)? {fell_back}")
        logged._print_diagnosis(fell_back=fell_back)
    except Exception as exc:  # noqa: BLE001
        print(f"  [예외] {exc!r}")

    _print_sub("N10 benefit_calculator (금액 추출 JSON)")
    try:
        amount, note = _extract_amount_via_llm(SAMPLE_POLICY_TEXT, logged)
        print(f"  amount: {amount}  (원문의 정답: 200000.0)")
        print(f"  note: {note}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [예외] {exc!r}")

    _print_sub("N13 answer_generation (답변 문장 다듬기)")
    try:
        result = generate_answer(_sample_state_for_n13(), llm_client=logged)
        draft = result.get("draft_answer")
        print(f"  draft_answer:\n{draft}")
        # generate_answer는 LLM 실패 시 템플릿 문장으로 조용히 폴백한다 -
        # 여기서 로그를 봐야 LLM이 실제로 응답했는지, 실패해서 템플릿이
        # 그대로 나온 건지 구분할 수 있다.
        if logged.last_error is not None:
            print(f"  [진단] LLM 호출 실패(그래서 템플릿 문장으로 폴백함): {logged.last_error!r}")
        elif logged.last_response is not None:
            print(f"  [진단] LLM이 실제로 응답함 (원문 그대로 썼는지는 위 draft_answer로 육안 확인)")
        else:
            print("  [진단] LLM 호출 자체가 안 됨 (llm_client=None이거나 sections가 비어 있었을 가능성)")
    except Exception as exc:  # noqa: BLE001
        print(f"  [예외] {exc!r}")


def main() -> None:
    print("HuggingFace Inference API로 N5/N9/N10/N13 프롬프트를 후보 모델에 테스트합니다.")
    print(f"후보 모델: {CANDIDATE_MODELS}\n")
    for model_name in CANDIDATE_MODELS:
        probe_model(model_name)


if __name__ == "__main__":
    main()
