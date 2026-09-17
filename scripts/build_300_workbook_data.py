"""Build plain-Korean workbook data; artifact-tool handles spreadsheet authoring."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

LABELS = {"qwen": "Qwen3.5-9B", "ax": "A.X-4.0-Light", "bllossom": "Bllossom-3B"}
CATEGORIES = {"normal": "일반 질문", "missing": "정보 부족", "boundary": "경계 조건",
              "subject": "질문 대상 구분", "unknown": "근거 없음", "trap": "잘못된 전제·모순·조작 요구"}
REASONS = {"answer": "답변 제공", "generation_failure": "답변 형식 실패", "model_abstained": "모델이 답변 보류",
           "quote_rejected": "원문 인용 검사에서 차단", "self_verifier_rejected": "모델 자체 검사에서 차단",
           "verifier_failure": "모델 자체 검사 실패"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def joined(items):
    return "\n".join(f"· {item}" for item in items)


def yn(value):
    return "예" if value is True else "아니요" if value is False else "평가 제외"


def chunks(text):
    # Each continuation fits a readable Excel row; preserve exact text by joining.
    text = str(text or "")
    if not text:
        return [""]
    result = []
    while text:
        end = min(len(text), 400)
        positions = [i for i, c in enumerate(text[:end]) if c == "\n"]
        if len(positions) >= 12:
            end = positions[11] + 1
        result.append(text[:end])
        text = text[end:]
    return result


def sheet(name, headers, rows, widths, note=""):
    return dict(name=name, title=name, note=note, headers=headers, rows=rows, widths=widths)


def questions(base):
    scenarios = read(base / "scenarios.json")
    cases = read(base / "cases.json")
    policies = read(base / "policies.json")
    source_rows = []
    for pid, p in policies.items():
        for field, text in p["detail"].items():
            for n, part in enumerate(chunks(text), 1):
                source_rows.append(["정책 " + pid, p["title"], field, n, part])
    return {"workbooks": [{"filename": "300문장_질문과_답변기준.xlsx", "title": "300문장 질문과 답변 기준", "sheets": [
        sheet("읽는 방법", ["항목", "설명"], [
            ["구성", "100가지 상황 × 같은 뜻의 표현 3개 = 300문장. 말투 10종은 각각 30문장입니다."],
            ["원문 범위", "저장소의 공개 평가용 정책 8개를 고정했습니다. 최신 제도 확인 자료는 아닙니다."],
            ["제작·검토", "네 작성 에이전트가 정책을 나눠 제작하고 별도 검토를 거쳤습니다. 실제 사용자 질문을 수집한 자료는 아닙니다."],
            ["답변 기준", "필수 내용은 표현을 그대로 따라 쓰라는 뜻이 아닙니다. 같은 의미의 정확한 답변을 허용합니다."],
            ["확인 불가", "원문에 없는 금액·기간·요건을 만들어내지 않아야 합니다. 일부 조건만으로 전체 자격을 확정하면 안 됩니다."],
            ["조건 이해", "N1은 서비스가 질문에서 개인 조건을 읽는 단계입니다. 지원하는 개인 조건이 있는 상황만 점수 평균에 포함합니다."],
            ["기존 자료", "기존 팀 질문 8개와 이전 45개 평가는 별도 보존했습니다. 이번 자료가 기존 파일을 대체하지 않습니다."],
            ["원시 기록", "실행 원문과 상세 채점 기록은 별도 원시기록 ZIP에 제공합니다."],
        ], [22, 100]),
        sheet("문장 300개", ["문장 ID", "상황 ID", "질문 종류", "말투", "질문", "정책"],
              [[c["id"], c["situation_id"], CATEGORIES[c["category"]], c["style"], c["question"], c["policy"]["title"]] for c in cases],
              [16, 13, 24, 20, 84, 34], "같은 상황 ID의 세 문장은 사실과 질문 내용이 같습니다."),
        sheet("상황별 답변 기준", ["상황 ID", "상황", "밝힌 조건", "답변에 필요한 내용", "피해야 할 단정", "확인할 정보", "참고 답변", "조건 이해 평가"],
              [[s["id"], s["title"], joined(s["facts"]), joined(s["required_points"]), joined(s["forbidden_points"]),
                joined(s["missing_info"]), s["reference_answer"], yn(s["n1_applicable"])] for s in scenarios],
              [13, 35, 45, 62, 54, 35, 76, 17]),
        sheet("상황별 근거", ["상황 ID", "정책 ID", "발췌 번호", "원문 발췌"],
              [[s["id"], "정책 " + s["policy_id"], n, part] for s in scenarios
               for n, q in enumerate(s["reference_quotes"], 1) for part in chunks(q)], [13, 25, 14, 110],
              "발췌는 원문에 실제 존재하는 문장입니다. 근거 없음 문제의 발췌는 자료의 범위를 보여줍니다."),
        sheet("정책 원문", ["정책 ID", "정책", "원문 항목", "이어지는 순서", "내용"], source_rows,
              [25, 38, 25, 18, 110], "읽기 편하게 긴 원문을 여러 행으로 나눴습니다. 같은 항목의 순서대로 연결하면 원문입니다."),
    ]}]}


def results(base):
    summary = read(base / "result_summary.json")
    rows = read(base / "reviewed_results.json")
    by_model = summary["models"]
    names = list(LABELS)
    metrics = [
        ("도움이 되고 근거에 맞는 최종 답변 / 300", "detail_success"),
        ("근거 없는 주장이 있는 최종 답변 / 300", "detail_unsupported"),
        ("세 말투 모두 성공한 상황 / 100", "all_three_success"),
        ("말투에 따라 성공 여부가 달라진 상황 / 100", "style_sensitive_situations"),
        ("세 말투 모두 실패한 상황 / 100", "all_three_failed"),
        ("조건 이해 평균 / 5점", "n1_mean"), ("조건 이해 점수에 포함한 문장 수", "n1_count"),
        ("질문에 없는 개인 조건을 추가 / 300", "n1_invented"),
        ("유효한 생성 답변의 표현 평균 / 5점", "expression_mean"), ("표현 점수에 포함한 답변 수", "expression_count"),
        ("유용하고 근거에 맞았지만 서비스가 차단", "helpful_supported_but_blocked"),
        ("상세 답변 시간 중간값 / 초", "detail_median_seconds"),
        ("조건 추출 시간 중간값 / 초", "n1_median_seconds"),
        ("모델 호출 수 (재시도·자체 검사 포함)", "calls"), ("호출 오류", "call_errors"),
        ("출력 길이 제한에 도달한 호출 수", "length_limited_calls"),
    ]
    overview = [[label, *[round(by_model[m][key], 2) if key.endswith("_seconds")
                         else by_model[m][key] for m in names]] for label, key in metrics]
    category_rows, style_rows = [], []
    for field, target in (("category", category_rows), ("style", style_rows)):
        for key in by_model[names[0]][field]:
            for m in names:
                item = by_model[m][field][key]
                target.append([CATEGORIES.get(key, key), LABELS[m], item["n"], item["success"], item["unsupported"]])
    situations = []
    for sid in sorted({r["situation_id"] for r in summary["situation_results"]}):
        selected = {r["model"]: r for r in summary["situation_results"] if r["situation_id"] == sid}
        situations.append([sid, CATEGORIES[selected[names[0]]["category"]], *[selected[m]["success_count"] for m in names]])
    detail, raw, n1 = [], [], []
    for entry in rows:
        model, case, result, score = entry["model"], entry["case"], entry["result"], entry["score"]
        for index, part in enumerate(chunks(result["detail"]["final"]["text"]), 1):
            detail.append([case["id"], LABELS[model], index, part, yn(score["detail_success"]), yn(score["detail_unsupported"]),
                           score["detail_note"], REASONS[result["detail"]["outcome_reason"]],
                           CATEGORIES[case["category"]], case["style"]])
        generation = result["detail"].get("generation")
        if generation and generation.get("answerable"):
            for index, part in enumerate(chunks(generation["answer"]), 1):
                raw.append([case["id"], LABELS[model], index, part, yn(score["raw_supported"]),
                            yn(score["raw_helpful"]), score["expression_score"],
                            yn(result["detail"]["final"]["kind"] != "answer")])
        n1.append([case["id"], LABELS[model], score["n1_score"], yn(score["n1_invented"]),
                   score.get("n1_error_origin", "구분 미기입"), score["n1_note"]])
    sheets = [
        sheet("결과 요약", ["항목", *LABELS.values()], overview, [65, 24, 24, 24],
              "AI 검토 참고 결과입니다. 300문장은 100상황의 말투 변형입니다. 실패에는 잘못된 답변·설명 누락·서비스 차단이 포함됩니다. 점수·건수는 상세 시트 수식으로 계산하며 긴 답변은 첫 행만 셉니다. 시간·호출 통계는 고정 실행 요약 값입니다."),
        sheet("질문 종류별", ["질문 종류", "모델", "문장 수", "성공", "근거 없는 주장"], category_rows, [32, 26, 15, 15, 22]),
        sheet("말투별", ["말투", "모델", "문장 수", "성공", "근거 없는 주장"], style_rows, [26, 26, 15, 15, 22],
              "각 말투 30문장입니다. 문장 길이·오타 등이 함께 다른 조건이므로 말투만의 순수 효과라고 단정할 수 없습니다."),
        sheet("상황별 안정성", ["상황 ID", "질문 종류", *LABELS.values()], situations, [15, 34, 23, 23, 23],
              "상황당 세 표현 중 성공한 수(0~3). 3이면 모든 표현 성공, 0이면 모든 표현 실패입니다."),
        sheet("최종 답변과 판정", ["문장 ID", "모델", "이어지는 순서", "최종 답변", "성공", "근거 없는 주장", "평가 이유", "서비스 처리", "질문 종류", "말투"],
              detail, [16, 24, 17, 86, 12, 18, 75, 28, 32, 26], "서비스가 실제 반환한 문장입니다. 긴 답변은 순서대로 여러 행에 나눴습니다. 집계는 이어지는 순서 1만 포함합니다."),
        sheet("생성 답변과 표현", ["문장 ID", "모델", "이어지는 순서", "모델 생성 답변", "근거에 맞음", "도움이 됨", "표현 / 5점", "서비스가 차단"],
              raw, [16, 24, 17, 100, 18, 18, 18, 18], "형식이 유효하고 답할 수 있다고 표시한 생성 답변만 포함합니다. 평균·건수는 첫 행만 포함합니다. 서비스가 차단은 최종 응답 종류가 답변이 아닌 경우입니다."),
        sheet("조건 이해 판정", ["문장 ID", "모델", "조건 이해 / 5점", "없는 개인 조건 추가", "오류가 생긴 단계", "평가 이유"],
              n1, [16, 24, 20, 23, 25, 105], "평균 대상이 아닌 문장은 점수 칸이 비어 있습니다. 원시 조건과 서비스 처리 결과는 원시기록 ZIP에서 볼 수 있습니다."),
    ]

    # Legacy JS places headers on row 4 and retains rows as QA expectations.
    # Empty source tables use one blank row, never an inverted range.
    def ref(index, col):
        return f"'{sheets[index]['name']}'!${col}$5:${col}${max(5, len(sheets[index]['rows']) + 4)}"

    def formula(index, cell, expression):
        sheets[index].setdefault("formulas", []).append({"cell": cell, "formula": "=" + expression})

    for index, source_col in ((1, "I"), (2, "J")):
        for row in range(5, len(sheets[index]["rows"]) + 5):
            criteria = f'{ref(4, source_col)},$A{row},{ref(4, "B")},$B{row},{ref(4, "C")},1'
            for col, extra in (("C", ""), ("D", f',{ref(4, "E")},"예"'),
                               ("E", f',{ref(4, "F")},"예"')):
                formula(index, f"{col}{row}", f"COUNTIFS({criteria}{extra})")

    for row in range(5, len(situations) + 5):
        for col in "CDE":
            formula(3, f"{col}{row}",
                    f'COUNTIFS({ref(4, "A")},$A{row}&"-*",{ref(4, "B")},{col}$4,'
                    f'{ref(4, "C")},1,{ref(4, "E")},"예")')

    for col, situation_col in zip("BCD", "CDE"):
        detail_filter = f'{ref(4, "B")},{col}$4,{ref(4, "C")},1'
        raw_filter = f'{ref(5, "B")},{col}$4,{ref(5, "C")},1'
        n1_filter = f'{ref(6, "B")},{col}$4'
        situation_range = ref(3, situation_col)
        expressions = [
            f'COUNTIFS({detail_filter},{ref(4, "E")},"예")',
            f'COUNTIFS({detail_filter},{ref(4, "F")},"예")',
            f'COUNTIFS({situation_range},3)',
            f'COUNTIFS({situation_range},">0",{situation_range},"<3")',
            f'COUNTIFS({situation_range},0)',
            f'IF({col}11=0,"",AVERAGEIFS({ref(6, "C")},{n1_filter}))',
            f'COUNTIFS({n1_filter},{ref(6, "C")},">=0",{ref(6, "C")},"<>")',
            f'COUNTIFS({n1_filter},{ref(6, "D")},"예")',
            f'IF({col}14=0,"",AVERAGEIFS({ref(5, "G")},{raw_filter}))',
            f'COUNTIFS({raw_filter},{ref(5, "G")},">=0",{ref(5, "G")},"<>")',
            f'COUNTIFS({raw_filter},{ref(5, "E")},"예",{ref(5, "F")},"예",{ref(5, "H")},"예")',
        ]
        for row, expression in enumerate(expressions, 5):
            formula(0, f"{col}{row}", expression)
    return {"workbooks": [{"filename": "300문장_세모델_비교결과.xlsx", "title": "300문장 세 모델 비교 결과", "sheets": sheets}]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--questions", action="store_true")
    args = parser.parse_args()
    value = questions(args.output) if args.questions else results(args.output)
    write_new(args.output / ("question_workbook.json" if args.questions else "result_workbook.json"), value)
