"""Package completed evaluation records into a new, verified ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


README = """# 300문장 비교 원시기록

100가지 상황을 말투 3개씩 바꾼 300문장을 세 모델에 입력했습니다.
문장별 결과는 총 900개입니다. 실제 사용자 표본이나 최신 정책 확인 자료는 아닙니다.

## 읽는 순서
1. PROTOCOL.md: 무엇을 어떻게 비교했는지 설명합니다.
2. cases.json / scenarios.json / policies.json: 질문, 답변 기준, 고정 원문입니다.
3. result_summary.json: 모델별·상황별 합계입니다.
4. reviewed_results.json: 질문과 실제 결과, AI 검토 이유를 연결한 기록입니다.
5. runs/*.jsonl: 실제 호출의 원시 출력, 최종 답변, 시간과 오류 기록입니다.
6. blind_review/scores_*.json: 모델 이름을 가리고 검토한 개별 판정입니다.
   review_model_mapping.json으로 가린 이름과 실제 모델을 연결할 수 있습니다.

## 해석할 때
- N1은 질문에서 개인 조건을 읽는 단계입니다. 평균에는 모델당 231문장이 들어갑니다.
- 상세 답변에는 N1 결과를 넣지 않았습니다. 두 단계를 각각 비교했습니다.
- 최종 답변 성공은 질문에 도움이 되면서 근거 없는 주장이 없는 경우입니다.
- 세 표현이 모두 성공한 상황과 세 표현이 모두 실패한 상황을 구분합니다.
- 표현 점수는 유효하게 생성된 답변만 대상으로 합니다. 모델마다 분모가 다릅니다.
- 서비스의 모델 자체 검사 통과가 정답이라는 뜻은 아닙니다. 별도 AI 검토로 원문과 대조했습니다.
- 연결 오류가 생긴 시도는 transport_failure 파일에 보존했습니다. 완료 결과와 별개입니다.
- earlier_attempt가 있으면, 재시작 전에 발생한 문제와 CPU/GPU 대조 기록입니다. 최종 점수에 섞지 않았습니다.
- 모델은 모두 Q4_K_M 양자화 버전입니다. 실행 장치 설정 차이와 실패 이력도 함께 읽어 주세요.
- 이 평가는 고정된 정책 상세 답변과 조건 추출의 비교입니다. 전체 검색 서비스 시험은 아닙니다.

manifest.json은 질문·코드·생성 설정의 고정 기록입니다.
runtime 관련 추가 문서는 장치 설정 조정과 그 이유를 설명합니다.
archive_sha256.json은 ZIP 안의 각 기록이 바뀌었는지 확인하는 해시 목록입니다.
모델 파일, 환경변수 값, 시스템 프롬프트가 포함될 수 있는 서버 전체 로그는 이 ZIP에 넣지 않았습니다.
기존의 팀 질문 8개 및 이전 비교 산출물은 별도 보존되어 있습니다.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = args.base.resolve(strict=True)
    summary = json.loads((base / "result_summary.json").read_text(encoding="utf-8-sig"))
    reviewed = json.loads((base / "reviewed_results.json").read_text(encoding="utf-8-sig"))
    if len(reviewed) != 900 or any(summary["models"][m]["sentences"] != 300 for m in ("qwen", "ax", "bllossom")):
        raise ValueError("All 900 completed and reviewed outputs are required")
    if args.output.suffix.lower() != ".zip" or args.output.exists():
        raise ValueError("Choose a new ZIP path; existing files are preserved")
    required = ["PROTOCOL.md", "manifest.json", "rubric.json", "cases.json", "scenarios.json",
                "policies.json", "dataset_review.json", "result_summary.json", "reviewed_results.json",
                "review_model_mapping.json", "REVIEW_INTERPRETATION.md", "report_notes.json"]
    selected = {base / name for name in required}
    for pattern in ("runs/*.json", "runs/*.jsonl", "blind_review/scores_*.json", "review/*.json",
                    "*profile*.json", "*ADDENDUM*.md", "RUNTIME_INCIDENT*.md", "runtime_evidence.json",
                    "preserved_initial_attempt_sha256.json", "runtime_wrapper_snapshot.py", "excluded_attempts.json"):
        selected.update(base.glob(pattern))
    content = {"읽는방법.md": README.encode("utf-8")}
    for path in sorted(selected):
        resolved = path.resolve(strict=True)
        name = resolved.relative_to(base).as_posix()
        if name in content or not resolved.is_file():
            raise ValueError(f"Duplicate or invalid source: {name}")
        content[name] = resolved.read_bytes()
    preserved = base / "preserved_initial_attempt_sha256.json"
    if preserved.exists():
        previous = base.parent
        for pattern in ("runs/*.jsonl", "runs/*.json", "diagnostics/*.jsonl", "diagnostics/*.json",
                        "interruption_*.json"):
            for path in sorted(previous.glob(pattern)):
                # Full server logs are deliberately excluded; JSON records contain
                # model identity, options and response data, without chat prompts.
                name = "earlier_attempt/" + path.relative_to(previous).as_posix()
                content[name] = path.read_bytes()
        excluded_path = base / "excluded_attempts.json"
        if excluded_path.exists():
            for entry in json.loads(excluded_path.read_text(encoding="utf-8")):
                source = (base / entry["relative_path"]).resolve(strict=True)
                if source == previous:
                    continue
                if not source.is_relative_to(previous) or source == base:
                    raise ValueError("Excluded attempt is outside the experiment")
                for pattern in ("runs/*.json", "runs/*.jsonl", "blind_review/scores_*.json",
                                "*profile*.json", "runtime_wrapper_snapshot.py", "RUNTIME_INCIDENT*.md"):
                    for path in sorted(source.glob(pattern)):
                        name = "earlier_attempt/" + source.name + "/" + path.relative_to(source).as_posix()
                        content[name] = path.read_bytes()
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in content.items()}
    content["archive_sha256.json"] = (json.dumps(hashes, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with ZipFile(args.output, "x", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in content.items():
            archive.writestr(name, payload)
    with ZipFile(args.output) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(content):
            raise ValueError("ZIP verification failed")
        if any(hashlib.sha256(archive.read(name)).hexdigest() != digest for name, digest in hashes.items()):
            raise ValueError("ZIP content differs from source records")
    print(json.dumps({"zip": str(args.output), "files": len(content), "verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
