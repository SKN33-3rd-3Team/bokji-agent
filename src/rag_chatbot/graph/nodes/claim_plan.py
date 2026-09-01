"""N5 후보별 Claim Plan.

Issue #16 (N4~N6): subsidy_chunks의 후보 정책들을 자격·금액·중복수급 claim으로
원자적으로 분해해 claim_plan을 반환한다. (LLM 단발 호출)

입력: GraphState["subsidy_chunks"]
출력: {"claim_plan": list[ClaimDraft]}

주의: 저장소에 아직 LLM 클라이언트 연동 컨벤션(langchain/openai 등)이 확정된
게 없다 (2026-08-29 기준 관련 코드 전무). 그래서 이 노드는 실제 LLM 호출을
직접 하지 않고, ``ClaimExtractor`` 인터페이스로 분리해뒀다 - graph.py 조립
시점에 실제 LLM 기반 구현체를 주입하면 된다. 지금은 테스트/스모크 체크용
FakeClaimExtractor(claim_plan.py 밖, 테스트 파일)로만 검증 가능하다.

law_check_required=True인 claim에는 required_aspects/required_law_sources도
채운다 (N7 리뷰 피드백 #3). required_law_sources는 정책 원문의 "근거법령"
섹션(있는 경우)을 law_source_resolver.py로 매칭해서 만든다 - N5는 법령
데이터에 직접 접근하지 않으므로, 이 매칭도 ``LawSourceResolver`` 인터페이스로
분리해서 주입받는다.

TODO(N5, 확인 필요):
    - "doc_check_required=False로 판정하는 기준"이 팀에서 아직 미정
      (결정사항 로그 참고). 기준이 정해지기 전까지는 보수적으로 항상 True.
    - 실제 LLM 기반 ClaimExtractor 구현은 팀 LLM 컨벤션 확정 후 별도 작업.
    - 보조금24 원본 데이터의 "근거법령" 필드가 대부분 비어있는 버그가 있어서
      (수집 코드 파라미터 오류, 팀에 보고함), 그게 고쳐지기 전까지는
      required_law_sources가 대부분 빈 리스트로 나올 수 있다 - 정상이다.
"""

from __future__ import annotations

from typing import Protocol

from .law_source_resolver import (
    LawSourceResolver,
    resolve_required_law_sources,
)
from ..state import ClaimDraft, GraphState, RetrievedChunk

CLAIM_TYPES = ("eligibility", "amount", "duplicate")


class ClaimExtractor(Protocol):
    """정책 청크 텍스트 하나에서 claim 초안들을 뽑아내는 인터페이스.

    실제 구현체(LLM 호출)는 graph.py 조립 시점에 주입한다. 이 노드 자체는
    "청크 -> claim 후보 리스트" 변환을 어떻게 하는지는 몰라도 된다.
    """

    def extract(self, *, policy_id: str, text: str) -> list[dict]:
        """[{"claim_type": ..., "law_check_required": bool, "reasons": [...],
        "required_aspects": [...]}]  (required_aspects는 law_check_required=True일
        때만 의미 있음)

        reasons는 반드시 text(원문)에서 "그대로 발췌"한 문장이어야 한다
        (의역 금지, 팀 확인 필요 - 확정되면 이 제약을 지우거나 바꾼다).
        N6(document_verification.py)이 이 문장이 원문에 실제로 있는지
        단순 텍스트 포함 여부로 대조하기 때문에, 의역하면 N6이 실제로는
        맞는 claim도 "근거 없음"으로 잘못 판정하게 된다.
        """
        ...


def _group_chunks_by_policy(
    subsidy_chunks: list[RetrievedChunk],
) -> dict[str, list[RetrievedChunk]]:
    grouped: dict[str, list[RetrievedChunk]] = {}
    for chunk in subsidy_chunks:
        grouped.setdefault(chunk.chunk.metadata["source_id"], []).append(chunk)
    return grouped


def _find_legal_basis_text(policy_chunks: list[RetrievedChunk]) -> str | None:
    for chunk in policy_chunks:
        if chunk.chunk.metadata.get("section_type") == "legal_basis":
            # chunk_document()는 검색 품질을 위해 텍스트 앞에
            # "{제목}\n지역: {지역}\n{섹션제목}\n\n"를 붙인다 (rag_design/
            # chunking.py의 _prefix()). 원본 근거법령 문자열만 필요하니
            # 그 접두어를 떼어낸다 - 접두어와 본문 사이는 항상 빈 줄(\n\n)로
            # 구분되고, 접두어 자체엔 \n\n이 안 나온다는 전제로 첫 \n\n
            # 기준으로 자른다.
            _, _, raw_content = chunk.chunk.text.partition("\n\n")
            return raw_content or chunk.chunk.text
    return None


def plan_claims(
    state: GraphState,
    extractor: ClaimExtractor,
    law_resolver: LawSourceResolver | None = None,
) -> dict:
    """subsidy_chunks를 claim 단위로 원자적으로 분해해 claim_plan을 채운다.

    law_resolver를 안 주면(None) required_law_sources는 항상 빈 리스트로
    나간다 - law_check_required 자체는 여전히 정상 작동한다 (선택적 기능).
    """

    subsidy_chunks = state.get("subsidy_chunks") or []
    chunks_by_policy = _group_chunks_by_policy(subsidy_chunks)

    # 정책별로 "근거법령" 청크를 미리 찾아서 required_law_sources를 계산해둔다
    # (매 claim마다 다시 계산하지 않게).
    required_law_sources_by_policy: dict[str, list] = {}
    if law_resolver is not None:
        for policy_id, policy_chunks in chunks_by_policy.items():
            legal_basis_text = _find_legal_basis_text(policy_chunks)
            required_law_sources_by_policy[policy_id] = resolve_required_law_sources(
                legal_basis_text, law_resolver
            )

    # 청크별 claim 추출은 서로 완전히 독립이라 순서대로 기다릴 이유가 없다.
    # 추출기가 prefetch를 지원하면(LLM 기반) 먼저 동시에 뽑아 캐시를 채우고,
    # 아래 루프는 캐시된 결과만 꺼내 쓴다.
    #
    # 실측(2026-08-31): 청크 5개를 순차로 부르면 292초. 호출 하나가 ~58초라
    # 동시에 부르면 가장 느린 하나만 기다리면 된다.
    #
    # 아래 루프와 **같은 조건으로 걸러야** 한다. legal_basis 청크는 claim을
    # 만들지 않으므로 미리 뽑아봐야 버리는 호출이 된다.
    prefetch = getattr(extractor, "prefetch", None)
    if prefetch is not None:
        prefetch(
            [
                (chunk.chunk.metadata["source_id"], chunk.chunk.text)
                for chunk in subsidy_chunks
                if chunk.chunk.metadata.get("section_type") != "legal_basis"
            ]
        )

    claim_plan: list[ClaimDraft] = []
    for chunk in subsidy_chunks:
        if chunk.chunk.metadata.get("section_type") == "legal_basis":
            # 근거법령 섹션 자체는 자격/금액/중복수급 claim이 아니라
            # required_law_sources를 만드는 재료로만 쓴다.
            continue

        # policy_id는 doc_id(합성 해시값)가 아니라 원본 source_id
        # (chunk.metadata["source_id"])를 쓴다 (N7 리뷰 피드백 반영) -
        # N7이 정책과 법령 근거가 같은 출처인지 이 값으로 대조하는데,
        # 법령 쪽도 같은 방식(원본 source_id)으로 식별되기 때문에 형태를
        # 맞춰야 비교가 가능하다.
        policy_id = chunk.chunk.metadata["source_id"]
        # 정책 하나가 여러 청크로 쪼개져 있을 수 있어서, claim_id는
        # policy_id가 아니라 청크 고유 chunk_id를 기준으로 유일하게 만든다
        # (policy_id는 여러 claim이 같은 정책을 가리키도록 공유되는 게 맞음).
        chunk_id = chunk.chunk.chunk_id
        raw_claims = extractor.extract(policy_id=policy_id, text=chunk.chunk.text)
        for index, raw in enumerate(raw_claims):
            claim_type = raw["claim_type"]
            law_check_required = bool(raw.get("law_check_required", False))

            claim: ClaimDraft = {
                "claim_id": f"{chunk_id}:{claim_type}:{index}",
                "policy_id": policy_id,
                "claim_type": claim_type,
                # 판정 기준 미정이므로 보수적으로 항상 True로 둔다
                # (Issue #16 "하지 않을 일" 참고).
                "doc_check_required": True,
                "law_check_required": law_check_required,
                "evidence_chunk_ids": [],
                "status": "pending",
                "reasons": list(raw.get("reasons", [])),
            }
            if law_check_required:
                claim["required_aspects"] = list(raw.get("required_aspects", []))
                claim["required_law_sources"] = required_law_sources_by_policy.get(
                    policy_id, []
                )
            claim_plan.append(claim)

    return {"claim_plan": claim_plan}
