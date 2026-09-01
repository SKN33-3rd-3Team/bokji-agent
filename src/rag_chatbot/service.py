"""Streamlit 등 프론트엔드가 이 모듈 하나만 가져다 쓰면 되는 진입점.

Streamlit 앱 자체는 **다른 브랜치에서 개발 중**이다(2026-08-31). 이 브랜치는
그 앱이 호출할 서비스 계층까지만 담당하므로, 여기서 화면 코드를 만들지
말고 ``ask()``/``answer_followup()``의 반환 스키마를 계약으로 유지할 것 -
필드를 지우거나 이름을 바꾸면 다른 브랜치가 깨진다.

이 파일은 새 로직을 만들지 않는다 - ``scripts/interactive_console_chat.py``가
콘솔에서 하던 일(실제 vectorDB 연결, ``HF_TOKEN`` 기반 LLM 클라이언트 구성,
``run_graph``/``resume_graph`` 호출)을 그대로 재사용하면서, 반환값을
프론트엔드가 화면을 그릴 수 있는 구조화된 dict로 바꿔주는 얇은 레이어다.
vectorDB 연결/LLM 클라이언트 생성 로직은 원래 ``interactive_console_chat.py``
안에 있었는데, 이 모듈로 옮기고 그 스크립트는 여기서 다시 가져다 쓰도록
바꿨다(같은 로직이 두 곳에 따로 있으면 나중에 한쪽만 고치고 잊어버리기
쉬워서, 한 군데로 모았다).

공개 함수 두 개면 된다:

    from src.rag_chatbot.service import ask, answer_followup

    response = ask("우리 아이가 5살인데 유치원비 지원되는 정책 있나요?", session_id)
    if response["status"] == "needs_input":
        # N3가 하드 게이트 슬롯(지역/성별/소득구간/장애여부/취업상태)을
        # 더 물어야 하는 상태. response["question"]을 보여주고, 사용자
        # 답변을 다시 answer_followup(session_id, 답변)으로 넘긴다.
        ...
    else:
        # response["policies"]가 정책 카드 리스트, response["final_answer"]가
        # 최종 안내 문장. 아래 "반환 형태" 절 참고.
        ...

## 실행 환경

``rag_design``/``src.rag_chatbot``를 import할 수 있어야 하므로, 레포 루트가
``sys.path``에 있어야 한다(``scripts/*.py``가 쓰는 것과 같은 관례:
``PYTHONPATH=.;src`` 또는 레포 루트에서 실행). Streamlit 앱을 레포 루트가
아닌 다른 위치에서 띄우는 경우를 대비해, 이 모듈은 import되는 순간 레포
루트를 ``sys.path``에 한 번만 방어적으로 추가한다(아래 코드 참고) - 이미
PYTHONPATH가 잡혀 있으면 아무 효과 없는 안전한 중복 삽입 방지 처리다.

## LLM 연결

``.env``(또는 셸 환경변수)의 ``HF_TOKEN``이 있으면 N5(claim_plan)/
N9(eligibility_verdict)/N10(benefit_calculator)/N13(answer_generation) 네
곳에 ``HuggingFaceInferenceClient``를 실제로 붙인다 - 이 네 곳이 현재
그래프에서 LLM을 주입할 수 있는 자리 전부다(2026-08-31 기준 코드 직접 확인:
N1 slot_parser·N7 evidence_gate는애초에 llm_client 인자 자체가 없다).
모델 이름은 ``LLM_MODEL_NAME`` 환경변수로 지정한다(예전 이름
``LLM_HF_MODEL``도 하위 호환으로 계속 읽는다 - 둘 다 없으면
``_DEFAULT_HF_MODEL``). ``HF_TOKEN``이 없으면 조용히 규칙 기반/템플릿
경로로만 동작한다(그래프가 LLM 없이도 항상 끝까지 도는 성질은 그대로).

## 반환 형태 (ChatResponse)

두 함수 모두 같은 모양의 dict를 돌려준다 - 프론트엔드는 ``status``만 보고
분기하면 된다.

    # 슬롯이 더 필요한 경우 (N3 interrupt)
    {"status": "needs_input", "question": "...", "session_id": "...",
     "missing_slots": [...]}

    # 끝까지 실행된 경우
    {"status": "answered", "answer_status": "complete"|"partial"|"abstained",
     "final_answer": "...", "final_citations": [...],
     "policies": [PolicyView, ...], "session_id": "..."}

``PolicyView`` 한 건은 첨부받은 두 화면(추천 결과 리스트 / 정책 상세)을
한 dict로 같이 그릴 수 있게 설계했다 - 리스트 화면은 상위 필드만, 상세
화면은 ``detail`` 아래 필드까지 쓰면 된다.

    {
        "rank": 1,                      # 리스트에서의 순서(1부터) - 충족 우선, 그 다음 금액 큰 순
        "policy_id": "...",              # 안정적인 정책 ID (source_id)
        "title": "영유아보육료 지원",      # 청크 원문 첫 줄에서 뽑음(대체 필드 없음)
        "badge": "가장 적합"|"자격 충족"|"확인 필요"|"자격 미충족",
        "eligibility_status": "충족"|"미충족"|"미확인",
        "eligibility_reasons": [...],
        "amount": 280000.0 | None,       # 원 단위 숫자. 주기(월/연/1회)는 모름 - 아래 "한계" 참고
        "amount_label": "월 최대 280,000원 (총 3,360,000원)" | "지원금액 확인 필요",
        "duplicate_status": "가능"|"불가"|"조건부"|"미확인",
        "duplicate_note": "..." | None,
        "needs_confirmation": ["..."],   # 시스템이 스스로 판정 못해 사람이 더 확인해야 하는 사유들
        "related_law": [{"law_name":..., "source_url":...}, ...],
        "detail": {
            "purpose": "..." | None,             # 목적
            "support_target": "..." | None,       # 지원대상
            "eligibility_criteria": "..." | None,  # 선정기준
            "support_details": "..." | None,       # 지원내용
            "application_method": "..." | None,    # 신청방법
            "application_period": "..." | None,    # 신청기한
            "legal_basis": "..." | None,           # 근거법령
            "region_names": [...] | None,
            "region_scope": "national"|"regional"|"unknown" | None,
            "age_start": int | None,
            "age_end": int | None,
            "organization": "..." | None,
            "source_url": "..." | None,
            "source_name": "..." | None,
        },
    }

## 한계 (숨기지 않음 - docs/PROJECT_COMPLIANCE.md)

- **O/X 재질문은 구현하지 않기로 확정했다(2026-08-31 팀 결정).** 첨부 이미지의
  "추가 필요 정보" O/X 질문(예: "기초생활수급자이신가요?")은 정책별로 한 번 더
  되묻고 그 답을 반영해 재판정하는 인터랙션인데, 현재 그래프(N1~N14)에는 그
  재질문 루프가 없다. N3는 정책 검색 "이전" 하드 게이트 슬롯(지역/생년월일/
  성별/소득구간/장애여부/취업상태)만 되묻는다. 이번 범위에서는 "미확인" 판정
  사유와 검증 범위를 ``needs_confirmation``에 텍스트로 노출하는 것까지만
  한다 - 프론트엔드는 이 필드를 "추가 확인이 필요한 항목" 안내로 렌더링하고,
  O/X 버튼은 만들지 않는다.
- ``eligibility_status``("충족")만 화면에 띄우면 **"모든 자격 조건을 만족한다"로
  읽힌다.** 실제로 N9가 대조하는 조건은 문서 metadata에 있는 연령 기준뿐이고,
  장애 여부·성별·소득·취업 상태는 문서 쪽에 구조화된 기준이 없어 비교조차
  하지 못한다(그래서 비장애인에게 장애인 정책이 "자격 충족"으로 뜬 적이 있다).
  ``verification_checked``/``verification_unchecked``/``verification_note``를
  함께 내려보내니 **정책 카드에 반드시 같이 노출할 것.** 조건을 실제로
  대조하려면 문서 metadata 확장 + 재색인이나 LLM 본문 대조가 필요하다(별도 작업).
- ``amount_label``은 이제 지원 주기와 한도 여부를 함께 표기한다(2026-08-31).
  N10이 원문에서 "월/연/1회", "최대/한도", "1인당/가구당"을 읽어 넘겨주고,
  월 단가와 지원 개월수가 둘 다 명시된 경우에만 총액까지 계산한다
  (예: ``"월 최대 200,000원 (총 2,400,000원)"``). 다만 원문에 주기가 안
  적혀 있으면 여전히 ``None``이고, **기간을 모르는데 12를 곱하지 않는다.**
- ``detail`` 섹션들은 ``state["subsidy_chunks"]``(의미 검색으로 찾은 청크
  일부)가 아니라, 정책 ID당 7개 section_type을 vectorDB에서 직접 재검색해서
  채운다(``result_assembly.py``의 ``_find_related_law``와 같은 패턴) - 이래야
  질문과 무관해 애초에 검색 안 된 섹션도 상세 화면에 다 나온다. 다만 그
  섹션 자체가 원천 데이터에 없으면(``field_status``가 missing/fetch_failed)
  ``None``으로 빠진다 - 지어내지 않는다.
- (2026-08-31 수정 완료, 기록 남김) ``result_assembly.py``의
  ``_find_related_law``와 N9/N10/N11이 전부 ``metadata_equals={"doc_id":
  policy_id, ...}``로 필터링하고 있었는데, ``policy_id``는 실제로는
  ``chunk.metadata["source_id"]``이고 ``doc_id``(``f"subsidy:{{service_id}}:
  {{version}}"``)와 다른 값이라 이 필터가 프로덕션에서 구조적으로 단 한 번도
  매치되지 않았다 - 그 결과 실제 vectorDB로 돌리면 자격판정이 항상
  ``"미확인"``으로만 나오는 심각한 버그였다(사용자가 직접 돌려서 10/10
  정책이 전부 미확인인 걸로 발견). 4개 프로덕션 파일 전부
  ``"source_id"``로 고쳤고, 이를 마스킹하던 테스트 픽스처들도 같이 고쳐
  실제로 이 버그를 잡아낼 수 있게 했다(자세한 내용은 프로젝트 메모리
  streamlit_service.md 참고). 이 모듈의 ``_fetch_policy_detail``은 처음부터
  ``"source_id"``로 필터링해서 애초에 이 버그의 영향을 받지 않았다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal, TypedDict

# Streamlit 등 다른 위치에서 이 모듈을 import해도 rag_design/src.rag_chatbot를
# 찾을 수 있도록, 레포 루트를 한 번만 방어적으로 sys.path에 추가한다.
# (scripts/*.py가 쓰는 것과 같은 관례 - 이미 PYTHONPATH가 잡혀 있으면
# 아무 효과 없다.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import os

from dotenv import load_dotenv

from rag_design.contracts import SourceType
from rag_design.embeddings import (
    HashEmbeddingProvider,
    SentenceTransformerKoreanProvider,
)
from rag_design.vector_store import (
    ChromaVectorStore,
    CollectionNotFoundError,
    VectorSearchFilter,
    VectorStoreConfig,
)

from .graph import build_graph, resume_graph, run_graph
from .llm import HuggingFaceInferenceClient, RecordingLLMClient
from .timing import TIMER, node_title

# 레포 루트의 .env에서 HF_TOKEN/LLM_MODEL_NAME 등을 읽는다(이미 셸에 직접
# 설정돼 있으면 그 값이 우선한다 - load_dotenv 기본값 override=False).
load_dotenv(_REPO_ROOT / ".env")

# 실제 서비스 vectorDB. rag_design/index_policy.py·scripts/manual_test_chain.py
# 의 실제 색인 경로/collection_prefix와 동일하게 맞춘다 - 다르게 쓰면 빈
# 컬렉션을 새로 만들 뿐 실제 데이터에 연결되지 않는다.
_REAL_VECTOR_DB_PATH = _REPO_ROOT / "data" / "vector_db"
_REAL_COLLECTION_PREFIX = "bokji_rag"
_REAL_EMBEDDING_DIMENSION = 128  # 컬렉션 메타데이터 rag_embedding_provider: "local-hash-v1:128"

# HF_TOKEN이 있는데 LLM_MODEL_NAME/LLM_HF_MODEL을 안 정한 경우의 기본 모델 -
# 세 후보(client.py 상단 docstring 참고) 중 가장 작아서 HuggingFace 서버리스
# Inference API에 "warm"하게 떠 있을 가능성이 제일 높은 걸로 잡았다.
_DEFAULT_HF_MODEL = "Bllossom/llama-3.2-Korean-Bllossom-3B"


def build_embedding_provider():
    """검색에 쓸 임베딩 provider를 만든다.

    **현재 색인된 vectorDB는 ``HashEmbeddingProvider``(local-hash-v1:128)로
    만들어져 있다.** 이 provider는 자기 docstring이 밝히듯 "테스트와 오프라인
    스모크 체크 전용"이라, 문자 n-gram 해시일 뿐 의미를 담지 않는다. 그래서
    "월세가 부담돼요"로 검색해도 유기질비료·입양축하금 같은 무관한 정책이
    올라온다(2026-08-31 실측) - 검색 품질 문제의 근본 원인이다.

    진짜 의미 검색을 하려면 ``SentenceTransformerKoreanProvider``
    (intfloat/multilingual-e5-base, 768차원)로 **전체를 재색인해야 한다**.
    임베딩 provider가 다르면 벡터 공간 자체가 달라서, 색인과 검색이 반드시
    같은 provider여야 한다(ChromaVectorStore가 fingerprint로 확인한다).

    재색인 방법(레포 루트에서, sentence-transformers 설치 필요):
        python -m rag_design.vector_cli index-documents \
            --persist-directory data/vector_db --collection-prefix bokji_rag \
            --embedding korean --source subsidy --snapshot-id <id> \
            --documents <documents.jsonl>

    재색인 후 ``.env``에 ``EMBEDDING_PROVIDER=korean``을 넣으면 이 함수가
    같은 provider로 연결한다. 기본값은 지금 색인된 상태에 맞춰 ``hash``다.
    """

    provider_name = (os.environ.get("EMBEDDING_PROVIDER") or "hash").strip().lower()
    if provider_name == "hash":
        return HashEmbeddingProvider(
            int(os.environ.get("EMBEDDING_DIMENSION") or _REAL_EMBEDDING_DIMENSION)
        )
    if provider_name == "korean":
        return SentenceTransformerKoreanProvider(
            os.environ.get("EMBEDDING_MODEL_NAME") or "intfloat/multilingual-e5-base",
            dimension=int(os.environ.get("EMBEDDING_DIMENSION") or 768),
        )
    raise SystemExit(
        f"EMBEDDING_PROVIDER={provider_name!r}는 지원하지 않습니다 "
        "('hash' 또는 'korean')."
    )


def connect_store() -> ChromaVectorStore:
    """실제 서비스 vectorDB(``data/vector_db``)에 연결한다 (색인은 안 함)."""

    if not _REAL_VECTOR_DB_PATH.exists():
        raise SystemExit(
            f"{_REAL_VECTOR_DB_PATH}가 없습니다. 레포 루트에 실제 vectorDB가 "
            "있는 컴퓨터에서 실행했는지 확인해 주세요."
        )
    return ChromaVectorStore(
        build_embedding_provider(),
        VectorStoreConfig(
            persist_directory=_REAL_VECTOR_DB_PATH,
            collection_prefix=_REAL_COLLECTION_PREFIX,
        ),
    )


def build_llm_client() -> RecordingLLMClient | None:
    """``HF_TOKEN``이 있으면 N1/N5/N9/N10/N13에 실제로 붙일 LLM 클라이언트를
    만든다. 없으면(기본 상태) 조용히 ``None``을 반환해서 네 노드 모두 규칙
    기반/템플릿 경로로 동작한다 - 이 서비스가 LLM 없이도 항상 끝까지 도는
    성질은 그대로 유지한다.

    모델 이름은 ``LLM_MODEL_NAME`` 환경변수를 먼저 보고, 없으면 예전 이름
    ``LLM_HF_MODEL``(``scripts/interactive_console_chat.py``가 쓰던 이름 -
    하위 호환으로 계속 지원), 그것도 없으면 ``_DEFAULT_HF_MODEL``을 쓴다.
    """

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        return None
    model = (
        os.environ.get("LLM_MODEL_NAME")
        or os.environ.get("LLM_HF_MODEL")
        or _DEFAULT_HF_MODEL
    )
    # 토큰 예산과 "생각 끄기"를 환경변수로 조절할 수 있게 한다.
    #
    # 왜: 추론형 모델(Qwen3.5 계열)은 답을 쓰기 전에 내부 사고에 토큰을 크게
    # 써서 호출 하나가 수십 초씩 걸린다(실측: N1 한 번에 50초). 기본
    # max_new_tokens=8192는 그 사고 길이를 감당하려고 올려둔 값이라,
    # 비추론형 모델을 쓰면 훨씬 낮춰도 되고 그만큼 빨라진다.
    max_new_tokens = int(os.environ.get("LLM_MAX_NEW_TOKENS") or 8192)

    # LLM_DISABLE_THINKING=1이면 provider에 "사고 과정을 끄라"고 요청한다.
    # Qwen3 계열 chat template이 지원한다고 알려진 파라미터인데, 이
    # HuggingFace Inference Providers 라우팅 경로에서 실제로 먹히는지는
    # 검증하지 못했다(샌드박스에서 huggingface.co에 접속할 수 없음).
    # 안 먹히면 조용히 무시되거나 에러가 나므로, 켠 뒤 체감 속도와
    # llm_status의 평균 호출 시간을 비교해서 판단할 것.
    extra_body = None
    if os.environ.get("LLM_DISABLE_THINKING") == "1":
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}

    # RecordingLLMClient로 감싼다. 노드들은 LLM 호출이 실패해도 규칙 기반으로
    # 조용히 폴백하기 때문에, 감싸지 않으면 "LLM이 한 번도 안 돌았는데 결과는
    # 멀쩡히 나오는" 상태를 아무도 모른다. 여기 모인 실패 사유를
    # ChatResponse["llm_status"]로 화면까지 올린다.
    return RecordingLLMClient(
        HuggingFaceInferenceClient(
            model=model, max_new_tokens=max_new_tokens, extra_body=extra_body
        )
    )


# 그래프/store/llm_client는 만드는 비용이 크다(vectorDB 연결, LLM 클라이언트
# 구성) - 요청마다 새로 만들지 않고 프로세스에서 한 번만 만들어 재사용한다.
# Streamlit에서는 get_graph()를 @st.cache_resource로 한 번 더 감싸는 걸
# 권장한다(스크립트 재실행마다 이 캐시가 초기화되는 걸 막기 위해).
_runtime_cache: dict[str, Any] = {}


def get_graph() -> Any:
    """조립된 그래프를 반환한다(프로세스당 한 번만 조립)."""

    if "graph" not in _runtime_cache:
        # 이 세 단계를 따로 재는 이유: "첫 실행이 느리다"의 범인이
        # vectorDB 인덱스 로딩인지 그래프 조립인지 구분되지 않으면
        # 엉뚱한 데를 최적화하게 된다.
        with TIMER.measure("startup:vectordb_connect"):
            store = connect_store()
        with TIMER.measure("startup:llm_client"):
            llm_client = build_llm_client()
        with TIMER.measure("startup:graph_build"):
            graph = build_graph(store, llm_client=llm_client)
        _runtime_cache["store"] = store
        _runtime_cache["llm_client"] = llm_client
        _runtime_cache["graph"] = graph
    return _runtime_cache["graph"]


def get_store() -> ChromaVectorStore:
    """상세 화면용 섹션 재검색에 쓰는 store. ``get_graph()``와 같은 인스턴스."""

    get_graph()
    return _runtime_cache["store"]


class PolicyDetail(TypedDict, total=False):
    purpose: str | None
    support_target: str | None
    eligibility_criteria: str | None
    support_details: str | None
    application_method: str | None
    application_period: str | None
    legal_basis: str | None
    region_names: list[str] | None
    region_scope: str | None
    age_start: int | None
    age_end: int | None
    organization: str | None
    source_url: str | None
    source_name: str | None


class PolicyView(TypedDict, total=False):
    rank: int
    policy_id: str
    title: str
    badge: str
    eligibility_status: str
    eligibility_reasons: list[str]
    # 이 판정이 실제로 대조한 조건 / 대조하지 못한 조건, 그리고 그걸 한 문장으로
    # 정리한 안내. eligibility_status만 화면에 띄우면 "충족"이 "모든 조건 만족"
    # 으로 읽히므로, 카드에 이 문구를 함께 노출할 것.
    verification_checked: list[str]
    verification_unchecked: list[str]
    verification_note: str | None
    amount: float | None
    amount_label: str
    # 금액의 성격. amount만 띄우면 월/연/1회, 확정/상한 구분이 안 된다.
    # amount_label에 이미 반영돼 있지만, 화면에서 따로 배지로 쓰고 싶을 때를
    # 위해 원자값도 함께 넘긴다. period: "month"|"year"|"once"|None
    amount_period: str | None
    amount_is_maximum: bool
    amount_per_unit: str | None
    amount_total: float | None
    duplicate_status: str
    duplicate_note: str | None
    needs_confirmation: list[str]
    related_law: list[dict]
    detail: PolicyDetail


class ChatResponse(TypedDict, total=False):
    status: Literal["needs_input", "answered"]
    session_id: str
    question: str
    missing_slots: list[str]
    answer_status: str | None
    final_answer: str | None
    final_citations: list[dict]
    policies: list[PolicyView]
    # 프론트엔드가 같은 결과를 JSON, 일반 문자열, Markdown 비교표 중 필요한
    # 형식으로 바로 사용할 수 있게 한다. output_json은 직렬화 전 dict다.
    output_json: dict
    output_text: str
    output_markdown: str
    # LLM이 이번 요청에서 실제로 돌았는지. 실패해도 노드들이 규칙 기반으로
    # 폴백해 결과는 정상적으로 나오기 때문에, 이 값을 화면에 표시하지 않으면
    # 사용자는 AI가 판단한 줄 안다. {"enabled", "model", "calls", "successes",
    # "failures", "messages"} 형태.
    llm_status: dict
    # 이번 요청의 단계별 소요 시간과 실제로 지나간 노드 경로.
    # {"phases": [{name, count, total_s, avg_s, share}...],
    #  "node_path": [{node, title, seconds}...]}
    timing: dict


# --- 정책 상세 섹션 재검색 (목적/지원대상/선정기준/지원내용/신청방법/신청기한/근거법령) ---

_DETAIL_SECTION_TYPES: list[tuple[str, str]] = [
    ("purpose", "목적"),
    ("support_target", "지원대상"),
    ("eligibility_criteria", "선정기준"),
    ("support_details", "지원내용"),
    ("application_method", "신청방법"),
    ("application_period", "신청기한"),
    ("legal_basis", "근거법령"),
]


def _extract_title(chunk_text: str) -> str:
    """chunking.py의 prefix 규칙(``f"{제목}\\n지역: ...\\n{heading_path}"``)에서
    첫 줄만 뽑는다."""
    return chunk_text.split("\n", 1)[0].strip()


def _strip_prefix(chunk_text: str) -> str:
    """chunk.text 앞에 붙는 "{제목}\\n지역: ...\\n{heading_path}\\n\\n" prefix를
    떼고 본문만 남긴다. prefix 구분자(빈 줄)가 없으면(형식이 다른 경우)
    원문을 그대로 돌려준다 - 지어내지 않는다."""
    _, sep, body = chunk_text.partition("\n\n")
    return body.strip() if sep else chunk_text.strip()


def _fetch_policy_detail(policy_id: str, store: Any, query_id: str) -> dict:
    """정책 상세 화면에 필요한 섹션 텍스트/메타데이터를 vectorDB에서 직접
    채운다.

    state["subsidy_chunks"]는 N4가 의미 검색으로 찾은 청크 몇 개뿐이라
    (질문과 관련된 섹션만), 상세 화면에 필요한 7개 섹션을 전부 보장하지
    못한다. 그래서 정책 하나당 section_type별로 직접 재검색한다
    (``result_assembly.py``의 ``_find_related_law``와 같은 패턴, 다만
    필터 키는 ``"source_id"``를 쓴다 - 위 모듈 docstring "한계" 절 참고).
    """
    sections: dict[str, str] = {}
    meta: dict = {}
    for section_type, label in _DETAIL_SECTION_TYPES:
        try:
            hits = store.search(
                SourceType.SUBSIDY,
                f"{policy_id} {label}",
                query_id=f"{query_id}-{policy_id}-detail-{section_type}",
                top_k=1,
                search_filter=VectorSearchFilter(
                    metadata_equals={"source_id": policy_id, "section_type": section_type}
                ),
            )
        except CollectionNotFoundError:
            hits = ()
        if not hits:
            continue
        chunk = hits[0].chunk
        sections[section_type] = _strip_prefix(chunk.text)
        if not meta:
            meta = {
                "title": _extract_title(chunk.text),
                "source_url": chunk.metadata.get("source_url"),
                "source_name": chunk.metadata.get("source_name"),
                "organization": chunk.metadata.get("organization"),
                "region_names": chunk.metadata.get("region_names"),
                "region_scope": chunk.metadata.get("region_scope"),
                "age_start": chunk.metadata.get("age_start"),
                "age_end": chunk.metadata.get("age_end"),
            }
    return {"sections": sections, **meta}


_VERDICT_RANK = {"충족": 0, "미확인": 1, "미충족": 2}
# "자격 충족"/"가장 적합"은 과대 주장이었다. N9가 실제로 대조하는 조건은 문서
# metadata에 있는 연령 기준뿐이라, 비장애인에게 장애인 정책이 "자격 충족"으로
# 뜨는 일이 있었다. 뱃지 문구를 "확인한 범위"를 말하는 쪽으로 바꾸고,
# 무엇을 확인/미확인했는지는 verification_* 필드로 화면에 함께 넘긴다.
_VERDICT_BADGE = {
    "충족": "일부 조건 확인",
    "미확인": "확인 필요",
    "미충족": "자격 미충족",
}
# 확인한 조건이 하나도 없는데 "충족"이 나온 경우(문서에 대조할 구조화 기준이
# 아예 없었던 경우) - 가장 약한 근거이므로 따로 표시한다.
_BADGE_NOTHING_CHECKED = "미검증"


def _verification_note(checked: list[str], unchecked: list[str]) -> str | None:
    """이 판정의 검증 범위를 사용자에게 보여줄 한 문장으로 만든다.

    N9가 넘겨준 값만 쓰고 여기서 추측하지 않는다. 옛 형식의 판정처럼 둘 다
    비어 있으면 아무 문구도 만들지 않는다(없는 사실을 지어내지 않기 위함).
    """

    if not checked and not unchecked:
        return None
    if not unchecked:
        return f"{', '.join(checked)} 조건을 확인했습니다."
    if not checked:
        return (
            "이 정책 문서에는 대조할 수 있는 구조화된 기준이 없어 "
            f"{', '.join(unchecked)} 조건을 확인하지 못했습니다. 원문을 직접 확인해주세요."
        )
    return (
        f"{', '.join(checked)} 조건만 확인했습니다. "
        f"{', '.join(unchecked)}은(는) 확인하지 못했으니 원문을 직접 확인해주세요."
    )


_PERIOD_LABELS = {"month": "월", "year": "연", "once": "1회"}
_PER_UNIT_LABELS = {"person": "1인당", "household": "가구당"}


def _won(amount: float) -> str:
    return f"{amount:,.0f}원"


def _format_amount_label(
    amount: float | None, status_note: str | None, benefit: dict | None = None
) -> str:
    """금액을 사용자가 오해 없이 읽을 수 있는 문구로 만든다.

    금액 숫자만 보여주면 "200,000원"이 월인지 연인지 1회인지, 확정인지
    상한인지 알 수 없어서 그만큼 받는다고 읽힌다. 원천 데이터의 42.5%가
    "최대/한도" 표현이라 특히 위험하다. N10이 원문에서 읽어둔 성격
    (``period``/``is_maximum``/``per_unit``/``total_amount``)을 그대로 붙인다.

    예: ``"월 최대 200,000원 (12개월 기준 총 2,400,000원)"``
    """

    if not isinstance(amount, (int, float)):
        return status_note or "지원금액 확인 필요"

    benefit = benefit or {}
    parts: list[str] = []
    per_unit = _PER_UNIT_LABELS.get(benefit.get("per_unit") or "")
    if per_unit:
        parts.append(per_unit)
    period = _PERIOD_LABELS.get(benefit.get("period") or "")
    if period:
        parts.append(period)
    if benefit.get("is_maximum"):
        parts.append("최대")
    parts.append(_won(float(amount)))

    label = " ".join(parts)
    total = benefit.get("total_amount")
    if isinstance(total, (int, float)) and float(total) != float(amount):
        label += f" (총 {_won(float(total))})"
    return label


def _build_policy_view(
    policy_id: str, entry: dict, *, store: Any, query_id: str, rank: int, is_top: bool
) -> PolicyView:
    eligibility = entry.get("eligibility") or {}
    verdict = eligibility.get("verdict", "미확인")
    reasons = eligibility.get("reasons", [])

    benefit = entry.get("benefit_amount")
    amount = benefit.get("amount") if benefit else None
    amount_label = _format_amount_label(amount, entry.get("status_note"), benefit)

    duplicate = entry.get("duplicate")
    duplicate_status = duplicate.get("status") if duplicate else "미확인"
    duplicate_note = duplicate.get("condition_note") if duplicate else entry.get("status_note")

    needs_confirmation: list[str] = []
    if verdict == "미확인":
        needs_confirmation.extend(reasons)
    if duplicate_status in ("미확인", "조건부") and duplicate_note:
        needs_confirmation.append(duplicate_note)
    # 자격 미확인 사유와 중복수급 미확인 사유가 우연히 같은 문장일 수 있다
    # (예: 둘 다 "재검색에서 해당 정책 근거를 다시 찾지 못함") - 순서는
    # 유지하면서 중복만 뺀다.
    needs_confirmation = list(dict.fromkeys(needs_confirmation))

    checked = eligibility.get("checked") or []
    unchecked = eligibility.get("unchecked") or []
    if verdict == "충족" and not checked:
        # 아무 조건도 대조하지 못했는데 "충족"이라고 부르면 안 된다.
        badge = _BADGE_NOTHING_CHECKED
    elif is_top and verdict == "충족":
        # "가장 적합"은 쓰지 않는다 - 확인한 조건이 연령뿐인데 최적이라고
        # 단정하는 표현이기 때문. 순위는 rank 필드가 이미 나타낸다.
        badge = "우선 검토"
    else:
        badge = _VERDICT_BADGE.get(verdict, "확인 필요")

    verification_note = _verification_note(checked, unchecked)
    if verification_note and verification_note not in needs_confirmation:
        # 화면의 "추가 확인 필요" 영역에도 올려서, 카드만 보고 판단하는
        # 사용자가 검증 범위를 놓치지 않게 한다.
        needs_confirmation.append(verification_note)

    detail_raw = _fetch_policy_detail(policy_id, store, query_id)
    sections = detail_raw.get("sections", {})

    return {
        "rank": rank,
        "policy_id": policy_id,
        "title": detail_raw.get("title") or policy_id,
        "badge": badge,
        "eligibility_status": verdict,
        "eligibility_reasons": reasons,
        "verification_checked": checked,
        "verification_unchecked": unchecked,
        "verification_note": verification_note,
        "amount": amount,
        "amount_label": amount_label,
        "amount_period": (benefit or {}).get("period"),
        "amount_is_maximum": bool((benefit or {}).get("is_maximum")),
        "amount_per_unit": (benefit or {}).get("per_unit"),
        "amount_total": (benefit or {}).get("total_amount"),
        "duplicate_status": duplicate_status,
        "duplicate_note": duplicate_note,
        "needs_confirmation": needs_confirmation,
        "related_law": entry.get("related_law", []),
        "detail": {
            "purpose": sections.get("purpose"),
            "support_target": sections.get("support_target"),
            "eligibility_criteria": sections.get("eligibility_criteria"),
            "support_details": sections.get("support_details"),
            "application_method": sections.get("application_method"),
            "application_period": sections.get("application_period"),
            "legal_basis": sections.get("legal_basis"),
            "region_names": detail_raw.get("region_names"),
            "region_scope": detail_raw.get("region_scope"),
            "age_start": detail_raw.get("age_start"),
            "age_end": detail_raw.get("age_end"),
            "organization": detail_raw.get("organization"),
            "source_url": detail_raw.get("source_url"),
            "source_name": detail_raw.get("source_name"),
        },
    }


def _rank_policies(policies: dict[str, dict]) -> list[tuple[str, dict]]:
    """충족을 먼저, 그다음 금액이 큰 순으로 정렬한다(첨부 이미지의 "가장
    적합"이 맨 위에 오는 리스트 순서와 맞추기 위한 휴리스틱 - 정책상
    "가장 적합"의 정의가 따로 있는 건 아니다)."""

    def sort_key(item: tuple[str, dict]) -> tuple[int, float]:
        _, entry = item
        verdict = (entry.get("eligibility") or {}).get("verdict", "미확인")
        benefit = entry.get("benefit_amount")
        amount = benefit.get("amount") if benefit else None
        return (_VERDICT_RANK.get(verdict, 1), -(amount or 0.0))

    return sorted(policies.items(), key=sort_key)


def _llm_status() -> dict:
    """이번 요청에서 LLM이 실제로 돌았는지 요약한다.

    화면에 "AI 분석 적용됨 / 규칙 기반으로만 처리됨"을 정직하게 표시하기 위한
    값이다. LLM이 안 붙었거나 전부 실패했는데도 결과만 멀쩡히 보여주면
    사용자는 AI가 판단한 줄 안다(docs/PROJECT_COMPLIANCE.md - 한계를 숨기지
    않는다).
    """

    client = _runtime_cache.get("llm_client")
    if client is None:
        return {
            "enabled": False,
            "model": None,
            "calls": 0,
            "successes": 0,
            "failures": 0,
            "messages": [
                "HF_TOKEN이 없어 LLM 없이 규칙 기반/템플릿 경로로만 처리했습니다."
            ],
        }
    if not isinstance(client, RecordingLLMClient):
        # 외부에서 직접 주입한 클라이언트 - 기록 기능이 없다.
        return {
            "enabled": True,
            "model": getattr(client, "model", None),
            "calls": None,
            "successes": None,
            "failures": None,
            "messages": [],
        }
    return client.summary()


def _reset_llm_recorder() -> None:
    """요청 하나의 LLM 성공/실패만 보도록 기록을 비운다."""

    client = _runtime_cache.get("llm_client")
    if isinstance(client, RecordingLLMClient):
        client.reset()


def _timing_report() -> dict:
    """이번 요청에서 어디에 시간이 들었는지.

    ``node_path``는 그래프가 실제로 지나간 노드 순서다 - 조건부 분기가
    있어서 어떤 경로로 갔는지는 실행해봐야만 안다.
    """

    return {
        "phases": TIMER.summary(),
        "node_path": [
            {"node": name, "title": node_title(name), "seconds": seconds}
            for name, seconds in TIMER.path()
        ],
    }


def _markdown_cell(value: object) -> str:
    """Markdown 표 셀을 깨뜨리는 구분자와 줄바꿈을 이스케이프한다."""

    if value is None:
        return "-"
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _build_output_markdown(policies: list[PolicyView]) -> str:
    """최종 정책 목록을 렌더링 가능한 Markdown 표로 만든다."""

    header = (
        "| 순위 | 정책명 | 자격 확인 | 지원금 | 중복수급 | 출처 |\n"
        "|---:|---|---|---|---|---|"
    )
    if not policies:
        return header + "\n| - | 확인된 정책 없음 | - | - | - | - |"

    rows: list[str] = []
    for policy in policies:
        source_url = (policy.get("detail") or {}).get("source_url")
        source = f"[원문]({source_url})" if source_url else "-"
        rows.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    policy.get("rank"),
                    policy.get("title"),
                    policy.get("eligibility_status"),
                    policy.get("amount_label"),
                    policy.get("duplicate_status"),
                    source,
                )
            )
            + " |"
        )
    return header + "\n" + "\n".join(rows)


def _build_output_text(
    policies: list[PolicyView], final_answer: str | None = None
) -> str:
    """최종 답변과 정책 비교 결과를 일반 문자열로 만든다."""

    sections: list[str] = []
    if isinstance(final_answer, str) and final_answer.strip():
        sections.append(final_answer.strip())

    if policies:
        lines = ["정책 비교"]
        for policy in policies:
            detail = policy.get("detail") or {}
            source_url = detail.get("source_url") or "출처 없음"
            lines.append(
                f"[{policy.get('rank', '-')}] {policy.get('title') or policy.get('policy_id')}"
                f" | 자격: {policy.get('eligibility_status', '미확인')}"
                f" | 지원금: {policy.get('amount_label', '지원금액 확인 필요')}"
                f" | 중복수급: {policy.get('duplicate_status', '미확인')}"
                f" | 출처: {source_url}"
            )
        sections.append("\n".join(lines))
    elif not sections:
        sections.append("확인된 정책이 없습니다.")

    return "\n\n".join(sections)


def _to_chat_response(result: dict, *, session_id: str, store: Any) -> ChatResponse:
    if "__interrupt__" in result:
        interrupt_payload = result["__interrupt__"]
        # graph.invoke()는 리스트, graph.stream()은 튜플로 준다 - 둘 다 첫
        # 원소의 .value가 질문 문자열이다.
        question = interrupt_payload[0].value
        missing_slots = result.get("missing_slots", [])
        return {
            "status": "needs_input",
            "question": question,
            "session_id": session_id,
            "missing_slots": missing_slots,
            "output_json": {
                "status": "needs_input",
                "session_id": session_id,
                "question": question,
                "missing_slots": missing_slots,
            },
            "output_text": (
                f"추가 정보가 필요합니다.\n{question}\n"
                f"부족한 정보: {', '.join(missing_slots) or '없음'}"
            ),
            "output_markdown": (
                "| 상태 | 추가 질문 | 부족한 정보 |\n"
                "|---|---|---|\n"
                f"| 추가 정보 필요 | {_markdown_cell(question)} | "
                f"{_markdown_cell(', '.join(missing_slots))} |"
            ),
            "llm_status": _llm_status(),
            "timing": _timing_report(),
        }

    policies_raw = (result.get("assembled_result") or {}).get("policies", {})
    query_id = result.get("query_id", session_id)
    ranked = _rank_policies(policies_raw)
    policy_views = [
        _build_policy_view(
            policy_id, entry, store=store, query_id=query_id, rank=i + 1, is_top=(i == 0)
        )
        for i, (policy_id, entry) in enumerate(ranked)
    ]

    output_json = {
        "status": "answered",
        "session_id": session_id,
        "answer_status": result.get("answer_status"),
        "final_answer": result.get("final_answer"),
        "final_citations": result.get("final_citations", []),
        "policies": policy_views,
    }
    final_answer = result.get("final_answer")

    return {
        "status": "answered",
        "session_id": session_id,
        "answer_status": result.get("answer_status"),
        "final_answer": final_answer,
        "final_citations": result.get("final_citations", []),
        "policies": policy_views,
        "output_json": output_json,
        "output_text": _build_output_text(policy_views, final_answer),
        "output_markdown": _build_output_markdown(policy_views),
        "llm_status": _llm_status(),
        "timing": _timing_report(),
    }


def ask(user_input: str, session_id: str, *, top_k: int = 5) -> ChatResponse:
    """새 대화를 시작한다(N1 진입점). Streamlit에서 사용자가 채팅창에 처음
    질문을 입력했을 때 호출한다.

    ``top_k``는 화면의 "정책 후보 수" 설정값이다. 기본값은 기존과 같은 5이고,
    그래프가 1~20 범위를 검증한다. 후속 답변에서는 첫 요청의 값이 LangGraph
    체크포인터에 보존되므로 다시 전달하지 않는다.
    """

    _reset_llm_recorder()
    TIMER.reset()
    with TIMER.measure("request_total"):
        graph = get_graph()
        store = get_store()
        result = run_graph(
            graph, user_input=user_input, session_id=session_id, top_k=top_k
        )
    return _to_chat_response(result, session_id=session_id, store=store)


def answer_followup(session_id: str, user_input: str) -> ChatResponse:
    """직전 ``ask()``(또는 ``answer_followup()``)가 ``status="needs_input"``을
    돌려준 세션을, 사용자의 답변으로 재개한다(N3 interrupt 재개). ``ask()``와
    같은 ``session_id``로만 호출할 수 있다 - 체크포인터에 해당 세션의 이전
    진행 상태가 없으면 LangGraph가 에러를 낸다."""

    _reset_llm_recorder()
    TIMER.reset()
    with TIMER.measure("request_total"):
        graph = get_graph()
        store = get_store()
        result = resume_graph(graph, session_id=session_id, user_input=user_input)
    return _to_chat_response(result, session_id=session_id, store=store)


__all__ = [
    "ask",
    "answer_followup",
    "connect_store",
    "build_embedding_provider",
    "build_llm_client",
    "get_graph",
    "get_store",
    "ChatResponse",
    "PolicyView",
    "PolicyDetail",
]
