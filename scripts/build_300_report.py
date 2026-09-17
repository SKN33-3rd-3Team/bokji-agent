"""Build a new Korean report from completed collect_300_results.py output.

Run with bundled Python/ReportLab after the parent finishes all 900 reviews
and the PDF artifact marker. This module has no import-time file operations.

Optional report_notes.json schema (all keys optional; text is plain text):
{
  "conclusions": ["검토를 마친 결론"],
  "examples": [{"id": "S001-style", "model": "qwen",
                "title": "사례 제목", "excerpt": "짧은 실제 원문",
                "comment": "검토자의 설명"}],
  "hardware": "최종 확인한 CPU, GPU, RAM 등",
  "runtime": {"qwen": "최종 백엔드와 실행 조건",
              "ax": "최종 백엔드와 실행 조건",
              "bllossom": "최종 백엔드와 실행 조건"},
  "runtime_caveats": ["실패 시도와 최종 실행에 관한 주의점"]
}
Only parent-provided notes supply conclusions, quotes and runtime facts.
Missing notes are explicitly marked pending; no conclusions are inferred.
Lists: at most 4 conclusions, 4 examples, 6 caveats. Text: 600 characters
per item, except excerpts (300), titles/IDs (100), hardware/runtime (800).
Oversize input or more than six laid-out pages fails before writing a PDF.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape


MODELS = {"qwen": "Qwen3.5-9B", "ax": "A.X-4.0-Light", "bllossom": "Bllossom-3B"}
CATEGORIES = {"normal": "일반 질문", "missing": "정보 부족", "boundary": "경계 조건",
              "subject": "질문 대상 구분", "unknown": "근거 없음", "trap": "잘못된 전제·모순·조작 요구"}
CATEGORY_COUNTS = dict(normal=75, missing=60, boundary=60, subject=45, unknown=30, trap=30)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate(summary, notes):
    models = summary["models"]
    if set(models) != set(MODELS):
        raise ValueError("Exactly qwen, ax and bllossom summaries are required")

    def count(value, maximum):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"Invalid count: {value!r}")

    for model, row in models.items():
        if (row["sentences"], row["situations"], row["n1_count"]) != (300, 100, 231):
            raise ValueError(f"{model}: require 300 sentences, 100 situations, N1 n=231")
        for key in ("detail_success", "detail_unsupported", "expression_count"):
            count(row[key], 300)
        count(row["all_three_success"], 100)
        for field in ("n1", "expression"):
            value = row[f"{field}_mean"]
            if row[f"{field}_count"] == 0 and value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 5:
                raise ValueError(f"{model}: invalid {field} mean")
        for field in ("detail_median_seconds", "n1_median_seconds"):
            value = row[field]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{model}: invalid latency")
        for field, groups in (("category", 6), ("style", 10)):
            if len(row[field]) != groups or set(row[field]) != set(models["qwen"][field]):
                raise ValueError(f"{model}: mismatched {field} groups")
            for name, group in row[field].items():
                quota = CATEGORY_COUNTS.get(name) if field == "category" else 30
                if not isinstance(name, str) or len(name) > 100 or group["n"] != quota:
                    raise ValueError(f"{model}: invalid {field} quota or label")
                count(group["success"], quota)
                count(group["unsupported"], quota)
            for key, total in (("success", "detail_success"), ("unsupported", "detail_unsupported")):
                if sum(g[key] for g in row[field].values()) != row[total]:
                    raise ValueError(f"{model}: {field} totals disagree")

    def text(value, limit=600):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"Notes must contain nonempty plain text of at most {limit} characters")

    if not isinstance(notes, dict):
        raise ValueError("report_notes.json must be an object")
    for field, limit in (("conclusions", 4), ("runtime_caveats", 6), ("examples", 4)):
        items = notes.get(field, [])
        if not isinstance(items, list) or len(items) > limit:
            raise ValueError(f"{field}: expected a list of at most {limit} items")
        for item in items:
            if field != "examples":
                text(item)
                continue
            if not isinstance(item, dict) or item.get("model") not in MODELS:
                raise ValueError("Example must identify a known model")
            for key, size in (("id", 100), ("title", 100), ("excerpt", 300), ("comment", 600)):
                text(item[key], size)
    if "hardware" in notes:
        text(notes["hardware"], 800)
    runtime = notes.get("runtime", {})
    if not isinstance(runtime, dict) or not set(runtime) <= set(MODELS):
        raise ValueError("runtime must map known model keys to plain text")
    for value in runtime.values():
        text(value, 800)


def render(summary, notes):
    # Use the bundled runtime's ReportLab; never import the old report builder.
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

    for name, filename in (("Malgun", "malgun.ttf"), ("MalgunBold", "malgunbd.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(Path("C:/Windows/Fonts") / filename)))
    navy = colors.HexColor("#17365D")
    styles = {
        "body": ParagraphStyle("body", fontName="Malgun", fontSize=10, leading=15,
                               spaceAfter=8, wordWrap="CJK"),
        "title": ParagraphStyle("title", fontName="MalgunBold", fontSize=17, leading=24,
                                textColor=navy, spaceAfter=12, keepWithNext=True, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", fontName="Malgun", fontSize=9.5, leading=14, wordWrap="CJK"),
        "head": ParagraphStyle("head", fontName="MalgunBold", fontSize=9.5, leading=14,
                               textColor=colors.white, wordWrap="CJK"),
    }
    story = []
    width = A4[0] - 86

    def para(value, style="body"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])

    def add(value, style="body"):
        story.append(para(value, style))

    def section(title):
        if story:
            story.append(PageBreak())
        add(title, "title")

    def table(headers, rows, first=200):
        widths = [first] + [(width - first) / (len(headers) - 1)] * (len(headers) - 1)
        grid = [[para(v, "head") for v in headers]]
        grid.extend([para(v, "cell") for v in row] for row in rows)
        result = Table(grid, colWidths=widths, repeatRows=1, hAlign="LEFT")
        result.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")]),
            ("LINEBELOW", (0, 0), (-1, -1), .3, colors.HexColor("#DCE4ED")),
        ]))
        story.extend([result, Spacer(1, 9)])

    rows = [summary["models"][m] for m in MODELS]
    headers = ["항목", "Qwen", "A.X", "Bllossom"]
    section("300문장 · 3개 모델 비교")
    add("최종 답변의 도움 여부와 근거 없는 주장, 질문 이해와 표현을 비교했습니다. 아래 수치는 완료된 900개 검토의 집계입니다.")
    table(headers, [
        ["도움이 되고 근거 없는 주장도 없음", *[f'{r["detail_success"]} / 300' for r in rows]],
        ["근거 없는 주장이 있음", *[f'{r["detail_unsupported"]} / 300' for r in rows]],
        ["세 말투 모두 성공한 상황", *[f'{r["all_three_success"]} / 100' for r in rows]],
        ["질문 이해(N1) 평균 / 5점", *[f'{r["n1_mean"]:.2f}\n(n={r["n1_count"]})' for r in rows]],
        ["표현 평균 / 5점", *[(f'{r["expression_mean"]:.2f}\n(n={r["expression_count"]})'
                               if r["expression_mean"] is not None else "평가 대상 없음 (n=0)") for r in rows]],
    ])
    add("실패에는 잘못된 답변, 필요한 설명 누락, 일반 안내문으로 차단된 경우가 모두 포함됩니다. 자료에 없는 주장이 실제로 나간 건수는 별도 항목으로 표시했습니다.")
    add("N1은 동결된 77개 상황 × 3개 말투 = 모델당 231개만 평가합니다. 표현 점수는 답변 생성 대상만 포함하므로 모델마다 분모가 다를 수 있습니다. 평균만으로 전체 답변 품질의 우열을 판단하지 마세요.")
    for item in notes.get("conclusions", []) or ["종합 결론: 검토자가 제공한 메모가 없어 작성을 보류했습니다."]:
        add(item)

    section("평가 방법과 범위")
    add("100개 상황에 같은 뜻의 표현 3개씩을 만들어 모델당 300문장을 평가했습니다. 질문 종류는 일반 75문장, 정보 부족 60문장, 경계 조건 60문장, 질문 대상 구분 45문장, 근거 없음 30문장, 잘못된 전제·모순·조작 요구 30문장입니다. 말투는 10종이며 각각 30문장입니다. 세 모델에 같은 질문과 고정된 정책 8개를 사용했습니다.")
    add("질문 이해(N1)는 질문의 조건을 읽는 단계입니다. 최종 답변 성공은 질문에 도움이 되면서 근거 없는 주장이 없는 경우입니다. 상황별 성공은 그 상황의 세 말투가 모두 성공했을 때만 셉니다.")
    add("이 평가는 고정 입력에 대한 모델 비교입니다. 검색부터 답변까지 이어지는 전체 RAG 서비스 시험이 아니며 사람이나 전문가의 판정도 아닙니다. 검토 점수와 원문을 함께 읽어야 합니다.")
    add("모델: " + " · ".join(MODELS.values()) + ". 세 모델 모두 Q4_K_M 양자화 버전입니다.")
    add("모델 이름을 가린 답변을 AI가 원문과 대조했습니다. 실제 사용자 표본이나 사람 평가가 아니며, 운영 서비스 전체나 최신 정책의 정확도를 보장하지 않습니다. 기존 보고서·질문 세트·실패 기록은 별도로 보존합니다.")

    section("범주별 결과")
    add("각 칸은 ‘성공 / 근거 없는 주장’의 개수입니다. 왼쪽에 해당 종류의 문장 수를 적었습니다. 두 수치를 더해 전체 문항 수로 해석하지 않습니다.")
    table(["범주", *headers[1:]], [
        [f'{CATEGORIES[key]} ({CATEGORY_COUNTS[key]}문장)', *[f'{r["category"][key]["success"]} / {r["category"][key]["unsupported"]}' for r in rows]]
        for key in sorted(rows[0]["category"])
    ], first=200)
    add("말투별 결과", "title")
    add("같은 표기이며 말투별 분모는 30문장입니다. 길이·오타 등이 함께 달라지므로 말투만의 순수한 효과라고 단정하지 않습니다.")
    table(["말투", *headers[1:]], [
        [key, *[f'{r["style"][key]["success"]} / {r["style"][key]["unsupported"]}' for r in rows]]
        for key in sorted(rows[0]["style"])
    ])

    section("실제 답변 사례")
    add("검토자가 제공한 짧은 원문 발췌만 표시합니다. 사례는 전체 결과를 대표한다고 단정할 수 없습니다.")
    for item in notes.get("examples", []):
        add(f'{item["title"]} | {item["id"]} | {MODELS[item["model"]]}')
        add("원문 발췌: " + item["excerpt"])
        add(item["comment"])
    if not notes.get("examples"):
        add("검토자가 제공한 사례가 없어 예시 작성을 보류했습니다.")

    section("실행 환경과 해석 시 주의점")
    add("하드웨어: " + notes.get("hardware", "최종 확인 메모 미제공"))
    for model, label in MODELS.items():
        add(label + " (Q4_K_M): " + notes.get("runtime", {}).get(model, "최종 백엔드·실행 조건 미제공"))
    table(["응답 시간 중간값 (초)", *headers[1:]], [
        ["질문 이해(N1)", *[f'{r["n1_median_seconds"]:.2f}' for r in rows]],
        ["최종 답변 단계", *[f'{r["detail_median_seconds"]:.2f}' for r in rows]],
    ])
    add("응답 시간은 참고 수치입니다. 백엔드가 서로 다르면 동일 조건의 속도 비교가 아닙니다. 최종 실행 환경이 확인되지 않았다면 모델 속도 순위를 결론 내릴 수 없습니다.")
    for item in notes.get("runtime_caveats", []) or ["실행 오류·재시도·최종 백엔드에 관한 검토 메모가 없어 상세 해석을 보류했습니다."]:
        add(item)

    def frame(canvas, doc):
        if doc.page > 6:
            raise ValueError("Report exceeds six A4 pages; shorten parent notes before retrying")
        canvas.saveState()
        canvas.setFont("Malgun", 9.5)
        canvas.setFillColor(navy)
        canvas.drawString(43, A4[1] - 30, "Bokji Agent | 300개 질문 모델 비교")
        canvas.setStrokeColor(colors.HexColor("#DCE4ED"))
        canvas.line(43, 43, A4[0] - 43, 43)
        canvas.drawString(43, 27, "고정 질문 평가 · AI 검토 참고 결과")
        canvas.drawRightString(A4[0] - 43, 27, str(doc.page))
        canvas.restoreState()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=43, rightMargin=43,
                            topMargin=53, bottomMargin=57, title="300개 질문 모델 비교", author="Bokji Agent")
    doc.build(story, onFirstPage=frame, onLaterPages=frame)
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("Output already exists; choose a NEW PDF path")
    if args.output.suffix.lower() != ".pdf":
        parser.error("Output must have a .pdf suffix")
    if not args.output.parent.is_dir():
        parser.error("Output parent directory must already exist")
    summary = read_json(args.base / "result_summary.json")
    notes_path = args.base / "report_notes.json"
    notes = read_json(notes_path) if notes_path.exists() else {}
    validate(summary, notes)
    payload = render(summary, notes)
    # Exclusive creation also rejects a destination created during rendering.
    with args.output.open("xb") as stream:
        stream.write(payload)
    print(args.output)


if __name__ == "__main__":
    main()
