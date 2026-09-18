"""Package completed evaluation records into a new, verified ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
if __package__:
    from .build_60_report import DEFAULT_BASE, DEFAULT_OUTPUT, load_selection, read_json, validate, validate_reviewed
else:
    from build_60_report import DEFAULT_BASE, DEFAULT_OUTPUT, load_selection, read_json, validate, validate_reviewed


README = """# 60문장 RunPod 비교 원시기록

원래 300문항에서 선택한 20가지 상황을 말투 3개씩 바꾼 60문장을 세 모델에 입력했습니다.
문장별 결과는 총 180개입니다. 실제 사용자 표본이나 최신 정책 확인 자료는 아닙니다.

## 읽는 순서
1. RUNTIME_ADDENDUM_60.md와 REVIEW_INTERPRETATION_60.md: 이번 60문장 비교 범위와 채점 기준입니다. PROTOCOL.md는 원래 300문항 계획입니다.
2. selection.json의 ordered_ids와 selected_cases.json / selected_scenarios.json: 선택 순서와 평가 대상입니다.
   cases.json / scenarios.json / policies.json은 원래 300문항의 고정 원본입니다.
3. result_summary.json: 모델별·상황별 합계입니다.
4. reviewed_results.json: 질문과 실제 결과, AI 검토 이유를 연결한 기록입니다.
5. runs/*.jsonl: 실제 호출의 원시 출력, 최종 답변, 시간과 오류 기록입니다.
6. blind_review/scores_*.json: 모델 이름을 가리고 검토한 개별 판정입니다.
   review_model_mapping.json으로 가린 이름과 실제 모델을 연결할 수 있습니다.
   amendments_*.json은 원래 점수를 보존한 채 바로잡은 검토 기록입니다. 지역명 확인 근거는 region_normalization_evidence.json에 있습니다.

## 해석할 때
- seed 60, 199번째 추출로 선택했습니다. 정책별 상황 수는 1/1/6/1/2/3/4/2로 달라 정책별 순위를 비교하지 않습니다.
- 독립 상황은 20개입니다. 60개 독립 표본이 아니며 선택 결과를 전체 300문항에 일반화하지 않습니다.
- RunPod NVIDIA RTX A5000 24GB에서 실행하며 A.X는 커뮤니티 Q4_K_M입니다. 서버 런타임은 프로필과 스냅샷에 보존합니다.
- 기존 로컬 GPU 실행 실패만으로 하드웨어 고장을 입증할 수 없습니다.
- N1은 질문에서 개인 조건을 읽는 단계입니다. 분모는 선택 입력의 n1_applicable에서 계산하며 요약에 표시합니다.
- 상세 답변에는 N1 결과를 넣지 않았습니다. 두 단계를 각각 비교했습니다.
- 최종 답변 성공은 질문에 도움이 되면서 근거 없는 주장이 없는 경우입니다.
- 세 표현이 모두 성공한 상황과 세 표현이 모두 실패한 상황을 구분합니다.
- 표현 점수는 유효하게 생성된 답변만 대상으로 합니다. 모델마다 분모가 다릅니다.
- 서비스의 모델 자체 검사 통과가 정답이라는 뜻은 아닙니다. 별도 AI 검토로 원문과 대조했습니다.
- 연결 오류가 생긴 시도는 transport_failure 파일에 보존했습니다. 완료 결과와 별개입니다.
- earlier_attempt가 있으면, 재시작 전에 발생한 문제와 CPU/GPU 대조 기록입니다. 최종 점수에 섞지 않았습니다.
- 먼저 실행한 RunPod A.X 41문장은 이번 점수에서 제외했습니다. 이어진 42번째 시도는 사용자 범위 변경에 따른 의도적 연결 중단이며 모델 고장으로 세지 않습니다.
- 모델은 모두 Q4_K_M 양자화 버전입니다. 실행 장치 설정 차이와 실패 이력도 함께 읽어 주세요.
- 이 평가는 고정된 정책 상세 답변과 조건 추출의 비교입니다. 전체 검색 서비스 시험은 아닙니다.

manifest.json은 질문·코드·생성 설정의 고정 기록입니다.
runtime 관련 추가 문서는 장치 설정 조정과 그 이유를 설명합니다.
archive_sha256.json은 ZIP 안의 각 기록이 바뀌었는지 확인하는 해시 목록입니다.
모델 파일, 환경변수 값, 시스템 프롬프트가 포함될 수 있는 서버 전체 로그는 이 ZIP에 넣지 않았습니다.
기존 45문항, 팀 질문 8개, 300문항 원본 및 실패 시도와 이전 비교 산출물은 별도 보존되어 있습니다.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT / "records.zip")
    args = parser.parse_args()
    base = args.base.resolve(strict=True)
    summary = json.loads((base / "result_summary.json").read_text(encoding="utf-8-sig"))
    reviewed = json.loads((base / "reviewed_results.json").read_text(encoding="utf-8-sig"))
    cases = load_selection(base)
    validate_reviewed(reviewed, cases)
    validate(summary, read_json(base / "report_notes.json"), cases)
    if args.output.suffix.lower() != ".zip" or args.output.exists() or args.output.is_symlink():
        raise ValueError("Choose a new ZIP path; existing files are preserved")
    required = [ "manifest.json", "rubric.json", "cases.json", "scenarios.json",
                "policies.json", "dataset_review.json", "result_summary.json", "reviewed_results.json",
                "review_model_mapping.json", "REVIEW_INTERPRETATION_60.md", "report_notes.json",
                "selected_cases.json", "selected_scenarios.json", "selection.json",
                "server_runpod.json", "excluded_attempts.json", "preserved_runpod_300_sha256.json",
                "region_normalization_evidence.json", "n1_scope_clarification.json"]
    selected = {base / name for name in required}
    for pattern in ("PROTOCOL.md", "runs/*.json", "runs/*.jsonl", "blind_review/*.json", "review/*.json",
                    "*profile*.json", "*ADDENDUM*.md", "RUNTIME_INCIDENT*.md", "runtime_evidence*.json",
                    "budget_*.json", "preservation_check*.json", "sequence_plan.json",
                    "preserved_initial_attempt_sha256.json", "*_snapshot.py", "excluded_attempts.json", "remote_gpu_monitor*.csv",
                    "runtime_profiles/*.json", "runtime_snapshots/*.json", "runtime_snapshots/*.py",
                    "runtime/*.json", "source/**/*.py", "source/**/*.json", "source/**/*.md",
                    "frozen_inputs/*.json", "frozen_inputs/*.md", "server_runtime*.json", "server_runtime*.txt", "*_snapshot.sh", "*_snapshot.ps1"):
        selected.update(base.glob(pattern))
    content = {"읽는방법.md": README.encode("utf-8")}
    if not any(base.glob("blind_review/scores_*.json")):
        raise ValueError("Blind grading records are required")
    if not any(base.glob("remote_gpu_monitor*.csv")):
        raise ValueError("Safe remote_gpu_monitor.csv evidence is required")
    if not any("profile" in path.name for path in selected) or not any("snapshot" in path.name for path in selected):
        raise ValueError("Saved runtime profiles and snapshots are required")
    for path in sorted(selected):
        resolved = path.resolve(strict=True)
        name = resolved.relative_to(base).as_posix()
        if name in content or not resolved.is_file():
            raise ValueError(f"Duplicate or invalid source: {name}")
        if any(part.startswith(".") for part in Path(name).parts) or resolved.suffix.lower() not in {".json", ".jsonl", ".md", ".py", ".csv", ".txt", ".sh", ".ps1"}:
            raise ValueError(f"Disallowed archive source: {name}")
        if any(word in name.lower() for word in ("secret", "credential", "server_log", "server.log", "auth.json")):
            raise ValueError(f"Disallowed archive source: {name}")
        content[name] = resolved.read_bytes()
    # Include exactly the source files bound by the original frozen manifest.
    # A changed checkout must never be presented as the frozen source.
    manifest = read_json(base / "manifest.json")
    root = Path(__file__).resolve().parents[1]
    for prefix, source_root, records in (
        ("", base, manifest["frozen_sha256"]),
        ("source/", root, manifest["source_sha256"]),
    ):
        for relative, expected in records.items():
            source = (source_root / relative).resolve(strict=True)
            source.relative_to(source_root)
            payload = source.read_bytes()
            if hashlib.sha256(payload).hexdigest() != expected:
                raise ValueError(f"Frozen manifest hash mismatch: {relative}")
            name = prefix + Path(relative).as_posix()
            if name in content and content[name] != payload:
                raise ValueError(f"Conflicting source snapshot: {name}")
            content[name] = payload
    protocol = "experiments/model_evaluation/local_300_20260916/PROTOCOL.md"
    content.setdefault("PROTOCOL.md", content["source/" + protocol])
    previous = (root / read_json(base / "excluded_attempts.json")["previous_base"]).resolve(strict=True)
    previous.relative_to(base.parent)
    for relative, expected in read_json(base / "preserved_runpod_300_sha256.json").items():
        source = (root / relative).resolve(strict=True)
        name = "earlier_attempt/" + source.relative_to(previous).as_posix()
        # Full logs, credentials and weights remain outside the archive.
        if source.suffix.lower() not in {".json", ".jsonl", ".csv", ".md", ".py", ".ps1", ".sh"}:
            continue
        payload = source.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError(f"Preserved attempt hash mismatch: {relative}")
        content[name] = payload
    # Check every member, including manifest-referenced sources, before writing.
    for name, payload in content.items():
        path = Path(name)
        if any(part.startswith(".") for part in path.parts) or path.suffix.lower() not in {
            ".json", ".jsonl", ".md", ".py", ".csv", ".txt", ".jinja2", ".ps1", ".sh"
        } or any(word in name.lower() for word in ("secret", "credential", "server_log", "server.log", "auth.json")):
            raise ValueError(f"Disallowed archive source: {name}")
        text = payload.decode("utf-8-sig")
        if re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-|hf_)[A-Za-z0-9_-]{20,}", text):
            raise ValueError(f"Possible secret in archive source: {name}")
        if path.suffix.lower() in {".json", ".jsonl"}:
            records = [json.loads(line) for line in text.splitlines() if line.strip()] if path.suffix.lower() == ".jsonl" else [json.loads(text)]
            def check(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key.lower() in {"api_key", "apikey", "access_token", "refresh_token", "password", "authorization", "private_key", "env", "environment_variables"} and item:
                            raise ValueError(f"Sensitive field in archive source: {name}")
                        check(item)
                elif isinstance(value, list):
                    for item in value:
                        check(item)
            for record in records:
                check(record)
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
