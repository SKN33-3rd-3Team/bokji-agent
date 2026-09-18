"""팀 질문 이해와 고정 안내문 표현 비교. 전체 검색·자격 판정 평가는 아니다."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts.eval_local_ollama import BASE_URL, MODELS, CaptureClient, api, dump, digest
from rag_chatbot.graph.nodes.slot_parser import parse_slots
from rag_chatbot.graph.nodes.slot_completeness_gate import check_slot_completeness
from rag_chatbot.graph.llm_gateway import generate_followup_question
from rag_chatbot.graph.nodes.answer_generation import generate_answer

AS_OF = date(2026, 9, 15)
SOURCE = "experiments/model_evaluation/local_20260915/team_source.json"
POLICIES = "data/evaluation/light_followup_policies.json"
OPTIONS = dict(num_ctx=8192, num_predict=1024, temperature=0, top_p=1, top_k=0,
               repeat_penalty=1, presence_penalty=0, frequency_penalty=0,
               seed=42, num_batch=128)
RUBRIC = {
    "version": 1,
    "method": "AI 검토용 기준입니다. 원시 출력과 제품 처리 결과를 함께 검토합니다. 실제 사람 평가가 아니며, runner는 점수를 자동 계산하지 않습니다.",
    "scale": {"5": "정확하고 근거에 충실하며 핵심을 전달함",
              "4": "경미한 누락이나 표현 문제가 있음",
              "3": "핵심 일부가 빠지거나 모호함",
              "2": "중요한 오해나 근거 없는 단정이 있음",
              "1": "응답 불능, 지어낸 수치, 또는 명령 주입 수행"},
    "dimensions": {
        "slots": "질문 원문에 맞게 본인과 자녀, 지역, 나이 등 슬롯을 이해했는지 평가합니다. 원시 N1 출력과 제품 슬롯 결과를 구분해 메모합니다.",
        "expression": "고정 N13 상태의 사실과 미확인 범위를 유지하며 핵심을 쉽게 전달하는지 평가합니다. 원시 응답의 오류가 제품에서 차단되었는지도 메모합니다.",
    },
    "question_weights": {str(number): 2 if number == 6 else 1 for number in range(1, 9)},
    "excluded_question_ids": {"slots": [], "expression": [2]},
    "aggregation": "슬롯과 표현을 따로 계산합니다. 각 평균은 합계(점수×가중치)/합계(가중치)입니다. 두 점수를 하나로 합치지 않습니다.",
    "missing_scores": "미채점은 0점이 아닙니다. 포함 대상이 모두 채점되기 전에는 최종 평균을 내지 않습니다.",
    "expected_weight_totals": {"slots": 9, "expression": 8},
    "reference_labels": "팀 예상 라벨은 참고용이며 정답이나 실제 시스템 판정으로 채점하지 않습니다.",
}
NOTE = (
    "질문 이해(N1)와 답변 표현(N13)을 따로 비교합니다. 검색, 자격·금액·중복수급 "
    "판정, N14 검증을 실행하는 전체 파이프라인 평가는 아닙니다. N13 상태는 "
    "현재 자료에 맞춰 평가 전 고정한 안내용 상태이며 실제 검색결과나 시스템 판정이 아닙니다. "
    "N1 출력은 N13에 연결하지 않습니다. 팀 예상 라벨은 입력 자료가 달라 정답으로 쓰지 "
    "않습니다. N3 되묻기는 규칙 문구여서 모델별 문체 점수에서 제외합니다. "
    "원래 질문을 N13 프롬프트에 덧붙이지 않으며 제품 프롬프트를 바꾸지 않습니다. "
    "N13 숫자 검사를 통과해도 모든 사실이 맞다는 뜻은 아니므로 원시 출력과 최종문을 "
    "함께 읽어 확인해야 합니다. 모델은 공통 runner의 실제 식별자를 따릅니다. "
    "rubric.json에 AI 검토용 슬롯·표현 각각의 1~5점 기준을 평가 전 고정합니다. 실제 사람 평가가 아닙니다. 질문 6의 가중치는 2, "
    "나머지는 1이며 질문 2는 표현 평균에서 제외합니다. 미채점은 0점으로 계산하지 않습니다."
    " 실행 전 32토큰 워밍업은 채점에서 제외하며, 단독 11435 서버에서 실행 전후 모델을 해제합니다."
)
REASONS = {
    1: "유아학비 문서는 있지만 자녀의 유치원 재원 여부, 국적과 기존 수급 여부는 확인되지 않았습니다. 보호자와 자녀의 나이를 구분해야 하므로 최종 지원자격 충족을 확정할 수 없습니다.",
    2: "청년 주거 지원을 찾는 질문이지만 신청자 정보가 부족합니다. 먼저 필요한 정보를 되묻습니다.",
    3: "자녀 생년월일을 본인 나이로 해석하면 안 됩니다. 유아학비 문서는 있지만 자녀의 유치원 재원 여부와 나머지 지원조건은 확인되지 않아 최종 자격은 미확인입니다.",
    4: "영유아 보육료 문서가 제공되지 않았습니다. 유아학비는 다른 정책이므로 대신 적용할 수 없고, 요청한 지원의 자격은 확인 불가입니다.",
    5: "청년 주거비 지원과 청년월세 특별지원 문서가 제공되지 않았습니다. 월세자금보증 문서로 두 정책의 중복수급 가능 여부를 확인할 수 없습니다.",
    6: "스마트팜 창업자금 문서가 없어 대출한도와 상환 유예기간은 확인 불가입니다. 다른 농어업 지원 문서의 숫자를 대신 쓰거나 추정할 수 없습니다.",
    7: "특정 정책을 지정하지 않았고 신청자 정보도 부족합니다. 현재 자료만으로 받을 수 있는 지원을 확정할 수 없습니다.",
    8: "한부모·다자녀 지원의 전체 목록 근거가 없습니다. 현재 자료만으로 받을 수 있는 지원 전부를 나열하거나 자격을 확정할 수 없습니다.",
}
SOURCE_FILES = [SOURCE, POLICIES, "scripts/eval_team_ollama.py", "scripts/eval_local_ollama.py",
                "src/rag_chatbot/graph/llm_gateway.py", "src/rag_chatbot/graph/slot_schema.py",
                "src/rag_chatbot/graph/nodes/slot_parser.py",
                "src/rag_chatbot/graph/nodes/slot_completeness_gate.py",
                "src/rag_chatbot/graph/nodes/answer_generation.py",
                "src/rag_chatbot/llm/ollama.py"]


def understand(question, client=None):
    # Product N1 calls extract_slots(question, {}, llm_client=client,
    # reference_date=AS_OF) and applies the real subject/date/region rules.
    state = {"user_input": question, "slots": {}, "as_of": AS_OF}
    result = parse_slots(state, llm_client=client)
    missing = check_slot_completeness({**state, **result})["missing_slots"]
    return {"slots": result["slots"], "missing_slots": missing,
            "followup": generate_followup_question(0, missing) if missing else None,
            "followup_style_scored": False}


def prepare(output):
    source = json.loads((ROOT / SOURCE).read_text(encoding="utf-8-sig"))
    policies = json.loads((ROOT / POLICIES).read_text(encoding="utf-8-sig"))
    if len(policies) != 8:
        raise ValueError("허용된 정책 문서 8개를 확인하세요")
    table = source["질문세트"]
    originals = [dict(zip(table[0], row)) for row in table[1:] if type(row[0]) is int]
    if [row["번호"] for row in originals] != list(range(1, 9)):
        raise ValueError("원문 질문 번호는 1~8이어야 합니다")
    cases = []
    for original in originals:
        number = original["번호"]
        real = number in (1, 3)
        pid = "000000465790" if real else "확인 불가"
        entry = {"eligibility": {"verdict": "미확인", "reasons": [REASONS[number]]},
                 "benefit_amount": None, "duplicate": {"status": "미확인"},
                 "status_note": "현재 자료로 지원금액 확인 불가"}
        state = {"assembled_result": {"policies": {pid: entry}}, "claim_plan": [],
                 "subsidy_chunks": [], "law_chunks": [], "node_trace": ["N12"]}
        cases.append({"id": number, "question": original["질문 문장"], "original": original,
                      "team_expected_reference_only": original["예상 판정"],
                      "review_expectation": REASONS[number],
                      "policy_id_is_synthetic": not real, "actual_search_result": False,
                      "evidence": [policies[pid]] if real else [], "state": state,
                      "n13_style_scored": number != 2,
                      "review_weight": RUBRIC["question_weights"][str(number)],
                      "baseline_n1": understand(original["질문 문장"]),
                      "baseline_n13": generate_answer(deepcopy(state)) if number != 2 else None})
    output.mkdir(parents=True, exist_ok=True)
    for name in ("cases.json", "manifest.json", "source.json", "rubric.json", "PREPARE_NOTE.md"):
        if (output / name).exists():
            raise FileExistsError(f"기존 동결 자료를 덮어쓰지 않습니다: {name}")
    dump(output / "source.json", source, exclusive=True)
    dump(output / "cases.json", cases, exclusive=True)
    dump(output / "rubric.json", RUBRIC, exclusive=True)
    (output / "PREPARE_NOTE.md").write_text(NOTE + "\n", encoding="utf-8")
    manifest = {"prepared_at": datetime.now(timezone.utc).isoformat(), "as_of": AS_OF.isoformat(),
                "note": NOTE, "models": dict(MODELS), "generation": OPTIONS,
                "disable_thinking": True, "timeout_seconds": 600,
                "python": platform.python_version(), "case_count": 8,
                "allowed_policy_ids": list(policies),
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_sha256": {name: digest((ROOT / name).read_bytes()) for name in SOURCE_FILES},
                "frozen_sha256": {name: digest((output / name).read_bytes())
                                  for name in ("cases.json", "source.json", "rubric.json", "PREPARE_NOTE.md")}}
    dump(output / "manifest.json", manifest, exclusive=True)
    print(f"질문 8개 동결 완료: {output}")


def evaluate(case, client):
    row = {"id": case["id"], "question": case["question"],
           "team_expected_reference_only": case["team_expected_reference_only"],
           "review_expectation": case["review_expectation"],
           "review": {"weight": case["review_weight"], "slots_score": None,
                      "expression_score": None, "expression_excluded": not case["n13_style_scored"],
                      "slots_note": "", "expression_note": ""},
           "actual_search_result": False, "policy_id_is_synthetic": case["policy_id_is_synthetic"]}
    client.calls = []
    started = time.perf_counter()
    result = understand(case["question"], client)
    row["n1"] = {**result, "calls": list(client.calls),
                 "ms": round((time.perf_counter() - started) * 1000, 2),
                 "baseline": case["baseline_n1"]}
    client.calls = []
    started = time.perf_counter()
    if case["id"] == 2:
        final = result["followup"]
        baseline = case["baseline_n1"]["followup"]
    else:
        final = generate_answer(deepcopy(case["state"]), llm_client=client)
        baseline = case["baseline_n13"]
    row["n13"] = {"final": final, "calls": list(client.calls), "baseline": baseline,
                  "ms": round((time.perf_counter() - started) * 1000, 2),
                  "style_scored": case["n13_style_scored"],
                  "route": "rule_followup" if case["id"] == 2 else "fixed_n13"}
    return row


def run(model_key, output):
    from rag_chatbot.llm.ollama import OllamaClient
    manifest_bytes = (output / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    for root, entries in ((ROOT, manifest["source_sha256"]), (output, manifest["frozen_sha256"])):
        for name, expected in entries.items():
            if digest((root / name).read_bytes()) != expected:
                raise ValueError(f"동결 자료가 변경되었습니다: {name}")
    if manifest["models"] != MODELS:
        raise ValueError("동결한 모델 목록과 현재 모델 목록이 다릅니다")
    destination = output / f"{model_key}.jsonl"
    metadata_path = output / f"{model_key}_metadata.json"
    if destination.exists() or metadata_path.exists():
        raise FileExistsError("기존 평가 결과를 덮어쓰지 않습니다")
    if BASE_URL != "http://127.0.0.1:11435":
        raise ValueError("팀 평가는 단독 http://127.0.0.1:11435 서버에서만 실행합니다")
    model = MODELS[model_key]
    tags = [tag for tag in api("/api/tags")["models"] if tag["name"] == model]
    if len(tags) != 1 or not tags[0].get("digest"):
        raise ValueError("설치된 모델과 digest를 확인하세요")
    show = api("/api/show", {"model": model})
    if show.get("details", {}).get("quantization_level") != "Q4_K_M":
        raise ValueError("모델 양자화가 Q4_K_M이 아닙니다")
    client = CaptureClient(OllamaClient(model=model, base_url=BASE_URL,
                                        timeout_seconds=manifest["timeout_seconds"],
                                        disable_thinking=manifest["disable_thinking"],
                                        **manifest["generation"]))
    metadata = {"model": model, "base_url": BASE_URL,
                "digest": tags[0]["digest"], "options": client.inner.options,
                "disable_thinking": True, "capabilities": show.get("capabilities"),
                "details": show.get("details"), "model_info": show.get("model_info"),
                "ollama": api("/api/version"), "python": platform.python_version(),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "manifest_sha256": digest(manifest_bytes),
                "cases_sha256": manifest["frozen_sha256"]["cases.json"]}
    dump(metadata_path, metadata, exclusive=True)
    cases = json.loads((output / "cases.json").read_text(encoding="utf-8"))
    try:
        metadata["running_models_before_unload"] = api("/api/ps").get("models", [])
        for running in metadata["running_models_before_unload"]:
            api("/api/generate", {"model": running["name"], "keep_alive": 0})
        metadata["running_models_after_initial_unload"] = api("/api/ps").get("models", [])
        try:
            client.complete('JSON 객체 하나로만 답하세요: {"ready": true}', max_tokens=32)
        finally:
            metadata["warmup"] = {"scored": False, "max_tokens": 32, "calls": list(client.calls)}
        metadata["running_models_after_warmup"] = api("/api/ps").get("models", [])
        dump(metadata_path, metadata)
        with destination.open("x", encoding="utf-8") as handle:
            for case in cases:
                row = evaluate(case, client)
                row["running_models"] = api("/api/ps").get("models", [])
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"{model_key}: 질문 {case['id']}/8 완료", flush=True)
    finally:
        try:
            api("/api/generate", {"model": model, "keep_alive": 0})
            metadata["running_models_after_final_unload"] = api("/api/ps").get("models", [])
        finally:
            dump(metadata_path, metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--model", choices=MODELS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.output) if args.prepare else run(args.model, args.output)


if __name__ == "__main__":
    main()
