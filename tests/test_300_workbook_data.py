"""In-memory contract checks; no experiment or workbook files are written."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "workbook_data", Path(__file__).resolve().parents[1] / "scripts/build_300_workbook_data.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ResultsFormulaTest(unittest.TestCase):
    def test_split_answers_and_missing_scores_keep_source_contract(self):
        keys = ("detail_success detail_unsupported all_three_success style_sensitive_situations "
                "all_three_failed n1_mean n1_count n1_invented expression_mean expression_count "
                "helpful_supported_but_blocked detail_median_seconds n1_median_seconds calls "
                "call_errors length_limited_calls").split()
        models, situations, reviewed = {}, [], []
        for model in builder.LABELS:
            models[model] = dict.fromkeys(keys, 0)
            models[model].update(n1_mean=None, expression_mean=2.5,
                                 expression_count=1, helpful_supported_but_blocked=1,
                                 detail_median_seconds=1.236)
            for field, value in (("category", "normal"), ("style", "정중")):
                models[model][field] = {value: {"n": 1, "success": 0, "unsupported": 0}}
            situations.append(dict(model=model, situation_id="S001", category="normal", success_count=0))
            reviewed.append(dict(
                model=model, case=dict(id="S001-1", category="normal", style="정중"),
                result={"detail": {"final": {"text": "가" * 801, "kind": "abstain"},
                                   "outcome_reason": "quote_rejected",
                                   "generation": {"answerable": True, "answer": "나" * 401}}},
                score=dict(detail_success=False, detail_unsupported=False, detail_note="검토",
                           raw_supported=True, raw_helpful=True, expression_score=2.5,
                           n1_score=None, n1_invented=False, n1_note="평가 제외")))
        summary = dict(models=models, situation_results=situations)
        with patch.object(builder, "read", side_effect=[summary, reviewed]), \
                patch.object(builder, "questions", side_effect=AssertionError("frozen")):
            sheets = builder.results(Path("unused"))["workbooks"][0]["sheets"]
        self.assertEqual(len(sheets), 7)
        self.assertEqual([len(s["rows"]) for s in sheets[4:]], [9, 6, 3])
        self.assertEqual(sheets[0]["rows"][5][1], None)
        self.assertEqual(sheets[0]["rows"][8][1], 2.5)
        self.assertEqual(sheets[0]["rows"][11][1], 1.24)
        self.assertTrue(all(r[-2:] == ["일반 질문", "정중"] for r in sheets[4]["rows"]))
        self.assertTrue(all(r[-1] == "예" for r in sheets[5]["rows"]))
        formulas = {f["cell"]: f["formula"] for f in sheets[0]["formulas"]}
        self.assertEqual(formulas["B5"],
                         '=COUNTIFS(\'최종 답변과 판정\'!$B$5:$B$13,B$4,'
                         '\'최종 답변과 판정\'!$C$5:$C$13,1,\'최종 답변과 판정\'!$E$5:$E$13,"예")')
        self.assertEqual(formulas["B10"],
                         '=IF(B11=0,"",AVERAGEIFS(\'조건 이해 판정\'!$C$5:$C$7,'
                         '\'조건 이해 판정\'!$B$5:$B$7,B$4))')
        self.assertIn("'생성 답변과 표현'!$C$5:$C$10,1", formulas["B13"])
        self.assertIn("'생성 답변과 표현'!$H$5:$H$10,\"예\"", formulas["B15"])
        self.assertIn('$A5&"-*"', sheets[3]["formulas"][0]["formula"])
        for sheet in sheets:
            self.assertTrue(all(len(r) == len(sheet["headers"]) for r in sheet["rows"]))
        # No generated answers is a valid population: bounded blank row, no fake zero mean.
        for entry in reviewed:
            entry["result"]["detail"]["generation"] = None
        with patch.object(builder, "read", side_effect=[summary, reviewed]):
            empty = builder.results(Path("unused"))["workbooks"][0]["sheets"]
        self.assertEqual(empty[5]["rows"], [])
        self.assertIn("$G$5:$G$5", empty[0]["formulas"][8]["formula"])


if __name__ == "__main__":
    unittest.main()
