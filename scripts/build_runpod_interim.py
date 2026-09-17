"""Exclusive interim sidecar. Parent runs artifact marker/render QA before --pdf.

Modes run separately: --prepare, workbook builder, --pdf, --zip.
Optional interim_notes.json: {"conclusions": ["..."], "examples":
[{"model": "ax", "id": "...", "quote": "exact short final-answer quote", "note": "..."}]}.
No mode overwrites an existing artifact. No production inputs are written.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import statistics
import sys
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.collect_60_results import DEFAULT_BASE, MODELS, load_selection, load_runs, read, write_new
from scripts.build_60_workbook_data import LABELS, CATEGORIES, chunks, sheet, yn

DEST = Path("output/llm_comparison_runpod_interim_20260916_0447_v2")
LIMIT = "중간결과: 세 모델이 모두 완료한 동일 문장만 비교합니다. 일부 말투·조건이 더 많아 전체 60문장 결과로 볼 수 없습니다. 같은 상황의 세 말투를 모두 맞힌 비율은 계산하지 않았습니다."
ORIGINS = {"오류 없음", "모델 출력", "공통 처리", "둘 다", "구분 어려움"}
ALLOWED = {"n1_score", "n1_error_origin", "n1_note", "detail_helpful", "raw_helpful", "detail_note"}


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def validate_score(score, case, row):
    for field in ("detail_helpful", "detail_unsupported", "n1_invented"):
        if type(score.get(field)) is not bool:
            raise ValueError(f"Invalid boolean: {field}")
    generated = bool(row["detail"].get("generation") and row["detail"]["generation"].get("answerable"))
    for field in ("raw_supported", "raw_helpful"):
        value = score.get(field)
        if (value is not None) != generated or (value is not None and type(value) is not bool):
            raise ValueError(f"Invalid raw applicability: {field}")
    for field, applicable in (("n1_score", case["n1_applicable"]), ("expression_score", generated)):
        value = score.get(field)
        if (value is not None) != applicable or (value is not None and (type(value) is not int or not 1 <= value <= 5)):
            raise ValueError(f"Invalid score/applicability: {field}")
    if not score.get("detail_note") or not score.get("n1_note") or score.get("n1_error_origin") not in ORIGINS:
        raise ValueError("Missing specific review rationale/error origin")
    score["detail_success"] = score["detail_helpful"] and not score["detail_unsupported"]


def reviews(base, cases, runs):
    mapping = read(base / "review_model_mapping.json")
    if set(mapping) != set(MODELS) or len(set(mapping.values())) != 3:
        raise ValueError("Invalid blinded model mapping")
    inverse = {v: k for k, v in mapping.items()}
    case_map = {c["id"]: c for c in cases}
    originals, hashes, amended_hashes, scores = {}, {}, {}, {}
    for path in sorted((base / "blind_review").glob("scores_*.json")):
        payload = path.read_bytes()
        source = json.loads(payload.decode("utf-8-sig"))
        hashes[path.name] = sha(payload)
        originals[path.name] = set()
        for raw in source["scores"]:
            key = (inverse[raw["label"]], raw["id"])
            if key in scores:
                raise ValueError(f"Duplicate review: {key}")
            score = dict(raw)
            validate_score(score, case_map[key[1]], runs[key[0]][key[1]])
            scores[key] = score
            originals[path.name].add(key)
    changed = set()
    for path in sorted((base / "blind_review").glob("amendments_*.json")):
        payload = path.read_bytes()
        amendment = json.loads(payload.decode("utf-8-sig"))
        amended_hashes[path.name] = sha(payload)
        source = amendment["source"]
        if source not in originals or amendment["source_sha256"] != hashes[source]:
            raise ValueError("Amendment original source hash mismatch")
        for change in amendment["changes"]:
            key = (inverse[change["label"]], change["id"])
            before, after = change["before"], change["after"]
            if key not in originals[source] or key in changed:
                raise ValueError("Duplicate amendment or wrong original record")
            if set(before) != set(after) or not set(after) <= ALLOWED or not change.get("reason"):
                raise ValueError("Invalid amendment fields/reason")
            if any(scores[key].get(k) != v for k, v in before.items()):
                raise ValueError("Amendment before differs")
            updated = dict(scores[key], **after)
            validate_score(updated, case_map[key[1]], runs[key[0]][key[1]])
            scores[key] = updated
            changed.add(key)
    return scores, hashes, amended_hashes


def prepare(base, dest):
    if dest.exists():
        raise FileExistsError("Prepare requires a new exclusive output directory")
    _, cases, selection_hash = load_selection(base)
    runs = load_runs(base, cases, allow_partial=True)
    counts = {m: len(runs.get(m, {})) for m in MODELS}
    n = min(counts.values())
    if not n:
        raise ValueError("All three models need a nonempty completed prefix")
    common = cases[:n]
    expected = {(m, c["id"]) for m in MODELS for c in common}
    scores, originals, amendments = reviews(base, cases, runs)
    if not expected <= scores.keys():
        raise ValueError(f"Shared prefix needs independent scores: {sorted(expected - scores.keys())}")
    rows = [{"model": m, "case": c, "result": runs[m][c["id"]], "score": scores[m, c["id"]]}
            for m in MODELS for c in common]
    stats = {}
    for m in MODELS:
        model_rows = [r for r in rows if r["model"] == m]
        values = [r["score"] for r in model_rows]
        calls = [call for r in model_rows for stage in ("n1", "detail") for call in r["result"][stage]["calls"]]
        elapsed = [sum(r["result"][stage]["elapsed_s"] for stage in ("n1", "detail")) for r in model_rows]
        stats[m] = {"completed": counts[m], "planned": 60, "common_count": n,
                    "excluded_completed": counts[m] - n,
                    "call_count": len(calls), "length_limit_calls": sum(c.get("done_reason") == "length" for c in calls),
                    "mean_seconds": statistics.mean(elapsed),
                    "outcome_counts": dict(Counter(r["result"]["detail"]["outcome_reason"] for r in model_rows))}
        for field in ("detail_success", "detail_unsupported", "n1_invented"):
            stats[m][field] = sum(s[field] for s in values)
        for field, prefix in (("n1_score", "n1"), ("expression_score", "expression")):
            nums = [s[field] for s in values if s[field] is not None]
            stats[m][prefix + "_count"] = len(nums)
            stats[m][prefix + "_mean"] = statistics.mean(nums) if nums else None
    summary = {"status": "중간결과", "prepared_at": datetime.now(timezone.utc).isoformat(),
               "base": str(base.resolve()), "planned_per_model": 60, "common_count": n,
               "common_ids": [c["id"] for c in common], "models": stats, "constraints": LIMIT,
               "category_counts": dict(Counter(c["category"] for c in common)),
               "style_counts": dict(Counter(c["style"] for c in common)),
               "manifest_sha256": sha((base / "manifest.json").read_bytes()),
               "selection_sha256": selection_hash, "review_sha256": originals,
               "amendment_sha256": amendments,
               "input_sha256": {p.relative_to(base).as_posix(): sha(p.read_bytes()) for p in
                                [base / "review_model_mapping.json", base / "server_runpod.json",
                                 base / "runtime_wrapper_snapshot.py",
                                 *[base / f"{m}_runtime_profile.json" for m in MODELS],
                                 *[base / "runs" / f"{m}.jsonl" for m in MODELS]]},
               "reviewed_common": rows}
    metrics = [("실제 완료 / 계획 60", "completed"), ("공통 비교 문장 수", "common_count"),
               ("완료했으나 이번 집계 제외·원본 보존", "excluded_completed"), ("최종 답변 성공 / 공통 N", "detail_success"),
               ("근거 없는 주장 / 공통 N", "detail_unsupported"), ("조건 이해 평균 / 5", "n1_mean"),
               ("조건 이해 평균 대상 수", "n1_count"), ("없는 개인 조건 추가 / 공통 N", "n1_invented"),
               ("생성문장 표현 평균 / 5", "expression_mean"), ("표현 평균 대상 수", "expression_count"),
               ("질문 1개 처리 평균 / 초", "mean_seconds"), ("총 모델 호출 수", "call_count"), ("출력 한도 도달 호출 수", "length_limit_calls")]
    answers, n1, execution = [], [], []
    for r in rows:
        c, s, m = r["case"], r["score"], LABELS[r["model"]]
        texts = [chunks(r["result"]["detail"]["final"]["text"]), chunks(s["detail_note"])]
        for i in range(max(map(len, texts))):
            answers.append([c["id"], m, i + 1, *[t[i] if i < len(t) else "" for t in texts],
                            yn(s["detail_success"]) if i == 0 else "", yn(s["detail_unsupported"]) if i == 0 else "",
                            s["expression_score"] if i == 0 else None])
        for i, part in enumerate(chunks(s["n1_note"]), 1):
            n1.append([c["id"], m, i, s["n1_score"] if i == 1 else None,
                       yn(s["n1_invented"]) if i == 1 else "", s["n1_error_origin"] if i == 1 else "", part])
        result = r["result"]
        calls = [call for stage in ("n1", "detail") for call in result[stage]["calls"]]
        from scripts.build_60_workbook_data import REASONS
        execution.append([c["id"], m, sum(result[stage]["elapsed_s"] for stage in ("n1", "detail")),
                          len(calls), sum(call.get("done_reason") == "length" for call in calls),
                          REASONS.get(result["detail"]["outcome_reason"], result["detail"]["outcome_reason"])])
    workbook = {"workbooks": [{"filename": "RunPod_중간_비교결과.xlsx", "sheets": [
        sheet("중간 요약", ["항목", *[LABELS[m] for m in MODELS]],
              [[label, *[stats[m][key] for m in MODELS]] for label, key in metrics], [62, 24, 24, 24], LIMIT),
        sheet("공통 질문", ["문장 ID", "상황 ID", "종류", "말투", "순서", "질문"],
              [[c["id"], c["situation_id"], CATEGORIES.get(c["category"], c["category"]), c["style"], i, part]
               for c in common for i, part in enumerate(chunks(c["question"]), 1)], [18, 16, 30, 24, 12, 95], LIMIT),
        sheet("최종 답변과 검토", ["문장 ID", "모델", "순서", "최종 답변", "구체적 검토 이유", "성공", "근거 없는 주장", "표현 점수"],
              answers, [18, 24, 12, 85, 85, 14, 20, 16], "긴 답변·검토 이유는 각각 순서대로 연결합니다. 점수는 첫 행만 표시합니다."),
        sheet("조건 이해 검토", ["문장 ID", "모델", "순서", "N1 점수", "없는 조건 추가", "오류 단계", "구체적 검토 이유"],
              n1, [18, 24, 12, 16, 20, 24, 100], "N1은 질문에서 나이·지역 등 개인 조건을 읽는 단계입니다. 빈 점수는 평균 제외입니다. 긴 이유는 순서대로 연결합니다."),
        sheet("실행 기록", ["문장 ID", "모델", "처리 시간 / 초", "모델 호출 수", "출력 한도 도달", "상세 답변 처리 결과"],
              execution, [18, 24, 24, 20, 22, 42], "조건 이해와 상세 답변의 합계입니다. 준비용 호출·모델 교체·예산 중단 당시 미완료 호출은 제외했습니다. 출처: 동봉한 runs 기록.")]}]}
    summary_sheet = workbook["workbooks"][0]["sheets"][0]
    summary_sheet["note"] += " 성공은 도움이 되며 근거 없는 주장이 없는 최종 답변입니다. 표현 점수는 생성된 문장만 평가하며 이후 차단된 답변도 포함합니다. 전 모델 Q4_K_M, 출력 1024, thinking 비활성화 요청."
    forms = summary_sheet.setdefault("formulas", [])
    for col, model in zip("BCD", MODELS):
        label = LABELS[model]
        def rng(name, column, count):
            return f"'{name}'!${column}$5:${column}${count+4}"
        a_model, n_model, e_model = rng("최종 답변과 검토", "B", len(answers)), rng("조건 이해 검토", "B", len(n1)), rng("실행 기록", "B", len(execution))
        def add(row, formula):
            forms.append({"cell": f"{col}{row}", "formula": "=" + formula})
        add(6, f'COUNTIFS({e_model},"{label}")')
        add(7, f'{col}5-{col}6')
        add(8, f'COUNTIFS({a_model},"{label}",{rng("최종 답변과 검토", "F", len(answers))},"예")')
        add(9, f'COUNTIFS({a_model},"{label}",{rng("최종 답변과 검토", "G", len(answers))},"예")')
        n_score = rng("조건 이해 검토", "D", len(n1))
        add(10, f'IF({col}11=0,"평가 제외",ROUND(SUMIFS({n_score},{n_model},"{label}",{n_score},">=1")/{col}11,2))')
        add(11, f'COUNTIFS({n_model},"{label}",{rng("조건 이해 검토", "D", len(n1))},">=1")')
        add(12, f'COUNTIFS({n_model},"{label}",{rng("조건 이해 검토", "E", len(n1))},"예")')
        expr_score = rng("최종 답변과 검토", "H", len(answers))
        add(13, f'IF({col}14=0,"평가 제외",ROUND(SUMIFS({expr_score},{a_model},"{label}",{expr_score},">=1")/{col}14,2))')
        add(14, f'COUNTIFS({a_model},"{label}",{rng("최종 답변과 검토", "H", len(answers))},">=1")')
        add(15, f'ROUND(AVERAGEIFS({rng("실행 기록", "C", len(execution))},{e_model},"{label}"),2)')
        for row, source_col in ((16, "D"), (17, "E")):
            add(row, f'SUMIFS({rng("실행 기록", source_col, len(execution))},{e_model},"{label}")')
    dest.mkdir(parents=True, exist_ok=False)
    write_new(dest / "interim_summary.json", summary)
    write_new(dest / "workbook_input.json", workbook)


def pdf(dest):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    summary = read(dest / "interim_summary.json")
    notes = read(dest / "interim_notes.json") if (dest / "interim_notes.json").exists() else {}
    conclusions = notes.get("conclusions", [])
    if isinstance(conclusions, str):
        conclusions = [conclusions]
    examples = notes.get("examples", [])
    if not isinstance(conclusions, list) or any(not isinstance(x, str) for x in conclusions):
        raise ValueError("conclusions must be text or a list of text")
    if len(conclusions) > 6 or sum(map(len, conclusions)) > 1800 or len(examples) > 3:
        raise ValueError("Keep interim notes short: <=6 conclusions/1800 characters, <=3 examples")
    pdfmetrics.registerFont(TTFont("Malgun", "C:/Windows/Fonts/malgun.ttf"))
    pdfmetrics.registerFont(TTFont("MalgunBold", "C:/Windows/Fonts/malgunbd.ttf"))
    style = ParagraphStyle("body", fontName="Malgun", fontSize=10, leading=16, wordWrap="CJK")
    heading = ParagraphStyle("heading", parent=style, fontName="MalgunBold", fontSize=17, leading=24, textColor=colors.HexColor("#17365D"))
    def p(text):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)
    story = [Paragraph("LLM 답변 품질 비교", heading), p(f'RunPod 중간결과 | 2026-09-16 | 동일 {summary["common_count"]}문장'), Spacer(1, 16), p(LIMIT), Spacer(1, 14)]
    table = [["모델", "실제 완료/계획", "공통 N", "집계 제외", "성공", "근거 없음"]]
    for m in MODELS:
        s = summary["models"][m]
        table.append([LABELS[m], f'{s["completed"]}/60', s["common_count"], s["excluded_completed"], s["detail_success"], s["detail_unsupported"]])
    t = Table([[p(v) for v in row] for row in table], colWidths=[110, 95, 62, 65, 60, 75])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 16)]
    for m in MODELS:
        s = summary["models"][m]
        def mean(key):
            return "평가 제외" if s[key] is None else f'{s[key]:.2f}'
        story += [p(f'{LABELS[m]}: 조건 이해 {mean("n1_mean")} / 5 ({s["n1_count"]}개), 없는 조건 추가 {s["n1_invented"]}개. 표현 {mean("expression_mean")} / 5 ({s["expression_count"]}개).')]
    story += [Spacer(1, 16), p("성공은 도움이 되면서 근거 없는 주장이 없는 최종 답변입니다. 조건 이해·표현 평균은 해당 점수가 있는 답변만 대상으로 하며 분모가 다를 수 있습니다."),
              p("모델명을 가린 검토 원본과 수정 이력은 보존했습니다. 문장별 답변과 검토 이유는 엑셀에서 확인할 수 있습니다. 파일 변경 여부를 확인하는 값과 원본은 ZIP에 있습니다."), PageBreak(), Paragraph("실행 조건과 해석 한계", heading), Spacer(1, 12),
              p("동일 RunPod NVIDIA RTX A5000 24GB에서 세 모델을 순차 실행했습니다. 모두 Q4_K_M 양자화 모델이며, A.X는 Ghiwook의 배포본입니다. 출력 제한은 호출당 1024토큰, 문맥은 8192토큰, temperature는 0입니다."),
              p("LLM_DISABLE_THINKING에 해당하는 설정은 true입니다. Qwen에는 think:false를 보냈고, 해당 옵션을 지원하지 않는 A.X와 Bllossom에는 생략했습니다. 완료된 모든 문장에서 모델 전체가 GPU 메모리에 올라간 기록을 확인했습니다."),
              p("Bllossom은 추가 사용 시간의 답변이 없어 한국시간 9월 16일 04:47에 중단했습니다. 안내한 04:40보다 중단이 늦었습니다. 그때의 미완료 호출은 별도 보존하고 점수에서 제외했습니다. Pod 자체는 중지하지 못했습니다."),
              p("질문 종류·말투의 균형과 상황당 세 표현 완성을 보장하지 않습니다. 전체 60문장·300문항·실제 사용자 성능으로 일반화하지 않습니다. 검색 전체 서비스 시험도 아닙니다."),
              p("100가지 상황을 세 말투씩 바꾼 300문장에서 20가지 상황·60문장을 골랐습니다. 이번 공통 44문장에는 8개 정책과 10가지 말투가 포함됩니다. 같은 상황의 표현 변형이 있어 서로 독립적인 44사례는 아닙니다. 팀원 질문 8개는 이전 결과로 보존하며 이번 점수에 합치지 않았습니다."),
              p("질문에서 나이·지역 등을 읽는 단계(N1)와 상세 답변은 따로 시험했습니다. 동일 정책 문서를 주었으며, 문서를 찾는 검색 단계는 시험하지 않았습니다. 모델명을 가린 별도 AI가 검토했고 일부 사례를 재확인했습니다. 사람 전문가의 확정 판정은 아닙니다."),
              p("표현 점수는 읽을 수 있는 생성문장에만 매겼습니다. 서비스 검사에서 차단된 문장도 포함하므로, 표현 점수가 높아도 사용자에게 좋은 답변이 전달됐다는 뜻은 아닙니다."), Spacer(1, 10)]
    for m in MODELS:
        s = summary["models"][m]
        story += [p(f'{LABELS[m]}: 같은 {summary["common_count"]}개 처리 평균 {s["mean_seconds"]:.1f}초, 모델 호출 {s["call_count"]}회 중 출력 한도 도달 {s["length_limit_calls"]}회.')]
    if conclusions or examples:
        story += [PageBreak(), Paragraph("결과 해석과 실제 답변", heading), Spacer(1, 12)]
    for text in conclusions:
        story += [p(text), Spacer(1, 8)]
    index = {(r["model"], r["case"]["id"]): r for r in summary["reviewed_common"]}
    for ex in examples:
        row = index[(ex["model"], ex["id"])]
        quote, note = ex["quote"], ex.get("note", "")
        if not quote or len(quote) > 240 or len(note) > 300 or quote not in row["result"]["detail"]["final"]["text"]:
            raise ValueError("Example needs an actual short final-answer quote and short note")
        story += [p(f'{LABELS[ex["model"]]} / {ex["id"]}: {quote}'), p(note), Spacer(1, 10)]
    buffer = io.BytesIO()
    pages = []
    def page_number(canvas, doc):
        pages.append(doc.page)
        canvas.setFont("Malgun", 8)
        canvas.drawRightString(559, 22, f"중간결과 · {doc.page}")
    SimpleDocTemplate(buffer, pagesize=(595.28, 841.89), leftMargin=36, rightMargin=36, topMargin=40, bottomMargin=40).build(
        story, onFirstPage=page_number, onLaterPages=page_number)
    if not 2 <= len(pages) <= 4:
        raise ValueError("PDF must be 2–4 pages; shorten parent notes")
    with (dest / "RunPod_중간_비교보고서.pdf").open("xb") as f:
        f.write(buffer.getvalue())


def package(base, dest):
    summary = read(dest / "interim_summary.json")
    load_selection(base)
    # Fail closed if records changed after preparation; reprepare in a fresh destination.
    expected = {"manifest.json": summary["manifest_sha256"], "selection.json": summary["selection_sha256"],
                **summary["input_sha256"], **{"blind_review/" + k: v for k, v in summary["review_sha256"].items()},
                **{"blind_review/" + k: v for k, v in summary["amendment_sha256"].items()}}
    content = {}
    suffixes = {".json", ".jsonl", ".md", ".csv"}
    for path in base.rglob("*"):
        if path.is_file() and (path.suffix.lower() in suffixes or "snapshot" in path.name and path.suffix.lower() in {".py", ".sh", ".ps1"}):
            path.resolve().relative_to(base.resolve())
            name = path.relative_to(base).as_posix()
            if any(part.startswith(".") for part in Path(name).parts):
                continue
            if any(word in name.lower() for word in ("secret", "credential", "server_log", "server.log", "auth.json", ".env")):
                continue
            content[name] = path.read_bytes()
    for name, digest in expected.items():
        if sha(content[name]) != digest:
            raise ValueError(f"Prepared source changed: {name}")
    for prefix, hashes in (("scores_", summary["review_sha256"]), ("amendments_", summary["amendment_sha256"])):
        if {p.name for p in (base / "blind_review").glob(prefix + "*.json")} != set(hashes):
            raise ValueError("Review inventory changed after preparation")
    manifest = read(base / "manifest.json")
    if len(manifest["source_sha256"]) != 80:
        raise ValueError("Expected original manifest's 80 source files")
    for prefix, root, records in (("", base, manifest["frozen_sha256"]), ("source/", Path(__file__).resolve().parents[1], manifest["source_sha256"])):
        for relative, digest in records.items():
            path = (root / relative).resolve(strict=True)
            path.relative_to(root.resolve())
            payload = path.read_bytes()
            name = prefix + Path(relative).as_posix()
            if sha(payload) != digest or (name in content and content[name] != payload):
                raise ValueError(f"Frozen source changed: {relative}")
            content[name] = payload
    for path in dest.iterdir():
        if path.is_file() and path.suffix.lower() in {".json", ".xlsx", ".pdf", ".md"}:
            content["interim/" + path.name] = path.read_bytes()
    for name in ("build_runpod_interim.py", "collect_60_results.py", "build_60_workbook_data.py", "run_60_runpod.py", "prepare_runpod_60.py", "finish_runpod_60_sequence.py", "build_llm_workbooks.mjs"):
        content["evaluation_tools/" + name] = (Path(__file__).parent / name).read_bytes()
    content["README.md"] = (f'# 중간결과 — 공통 {summary["common_count"]}/60문장\n\n' + LIMIT + "\n\n" +
        json.dumps(summary["models"], ensure_ascii=False, indent=2) +
        "\n전체 현재 완료 기록·고정 전체 입력·검토 원본 및 수정 이력은 보존합니다. 공통 구간 밖 점수는 중간 통계에서 제외합니다.\n").encode("utf-8")
    def sensitive(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"api_key", "apikey", "access_token", "refresh_token", "password", "authorization", "private_key", "env", "environment_variables"} and item:
                    raise ValueError("Sensitive JSON field in archive")
                sensitive(item)
        elif isinstance(value, list):
            for item in value:
                sensitive(item)
    for name, payload in content.items():
        path = Path(name)
        if path.is_absolute() or any(p.startswith(".") for p in path.parts) or path.suffix.lower() not in suffixes | {".py", ".mjs", ".jinja2", ".sh", ".ps1", ".xlsx", ".pdf"} or any(w in name.lower() for w in ("secret", "credential", "server_log", "server.log", "auth.json", ".env")):
            raise ValueError(f"Disallowed archive member: {name}")
        if path.suffix.lower() not in {".xlsx", ".pdf"}:
            text = payload.decode("utf-8-sig")
            if re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-|hf_)[A-Za-z0-9_-]{20,}", text):
                raise ValueError(f"Possible secret: {name}")
            if path.suffix.lower() == ".json":
                sensitive(json.loads(text))
            elif path.suffix.lower() == ".jsonl":
                # A partial trailing write is retained byte-for-byte; not counted as complete.
                for line in text.splitlines(keepends=True):
                    if line.strip() and line.endswith("\n"):
                        sensitive(json.loads(line))
    hashes = {name: sha(payload) for name, payload in content.items()}
    content["archive_sha256.json"] = json.dumps(hashes, ensure_ascii=False, indent=2).encode("utf-8")
    target = dest / "RunPod_중간_원시기록.zip"
    with ZipFile(target, "x", compression=ZIP_DEFLATED) as archive:
        for name, payload in content.items():
            archive.writestr(name, payload)
    with ZipFile(target) as archive:
        if archive.testzip() or set(archive.namelist()) != set(content) or any(sha(archive.read(name)) != digest for name, digest in hashes.items()):
            raise ValueError("ZIP SHA/CRC verification failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output", "--destination", type=Path, default=DEST)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ("prepare", "pdf", "zip"):
        modes.add_argument("--" + mode, action="store_true")
    args = parser.parse_args()
    if args.output.resolve() == args.base.resolve() or args.base.resolve() in args.output.resolve().parents:
        parser.error("Output must be separate from frozen experiment inputs")
    if args.prepare:
        prepare(args.base, args.output)
    elif args.pdf:
        pdf(args.output)
    else:
        package(args.base, args.output)


if __name__ == "__main__":
    main()
