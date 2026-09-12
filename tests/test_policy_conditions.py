from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

from rag_design.contracts import (
    Chunk,
    RetrievedChunk,
    SCHEMA_VERSION,
    SourceType,
    compute_content_hash,
)

from src.rag_chatbot.graph import policy_conditions
from src.rag_chatbot.graph.policy_conditions import (
    evaluate_conditions,
    filter_candidates,
    load_policy_user_types,
    load_support_conditions,
    matches_profile,
)
from src.rag_chatbot.graph.slot_schema import resolve_filter_slots


_CODES = (
    "JA0101",
    "JA0102",
    "JA0201",
    "JA0202",
    "JA0203",
    "JA0204",
    "JA0205",
    "JA0326",
    "JA0327",
    "JA0328",
    "JA0313",
    "JA0314",
    "JA0315",
    "JA0316",
    "JA0317",
    "JA0318",
    "JA0319",
    "JA0320",
    "JA0322",
    "JA1101",
    "JA1102",
    "JA1103",
)


def _row(source_id: str, *active: str) -> dict[str, str | None]:
    return {
        "서비스ID": source_id,
        **{code: "Y" if code in active else None for code in _CODES},
    }


def _wrapper(*rows: dict, match_count: int = 1, current_count: int = 1) -> dict:
    return {
        "matchCount": match_count,
        "currentCount": current_count,
        "data": list(rows),
    }


def _plan(**slots):
    return resolve_filter_slots(slots)


class SupportConditionsLoaderTests(unittest.TestCase):
    def _load(self, payload: object):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            policy_conditions, "_LOAD_WARNING_EMITTED", False
        ), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            path = Path(temp_dir) / "gov24_support_conditions.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return load_support_conditions(path)

    def test_loads_exact_single_row_wrapper(self) -> None:
        loaded = self._load([_wrapper(_row("service-1", "JA0102", "JA0202"))])

        self.assertEqual(loaded["service-1"]["JA0102"], "Y")
        self.assertIsNone(loaded["service-1"]["JA0101"])

    def test_loads_canonical_flat_rows_alongside_legacy_wrappers(self) -> None:
        loaded = self._load(
            [
                _row("flat", "JA0101"),
                _wrapper(_row("wrapped", "JA0102")),
            ]
        )

        self.assertEqual(set(loaded), {"flat", "wrapped"})
        self.assertEqual(loaded["flat"]["JA0101"], "Y")
        self.assertEqual(loaded["wrapped"]["JA0102"], "Y")

    def test_missing_or_invalid_file_fails_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            policy_conditions, "_LOAD_WARNING_EMITTED", False
        ), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            missing = Path(temp_dir) / "missing.json"
            invalid = Path(temp_dir) / "invalid.json"
            invalid.write_text("not-json", encoding="utf-8")

            self.assertEqual(load_support_conditions(missing), {})
            self.assertEqual(load_support_conditions(invalid), {})

    def test_unavailable_sidecar_warns_once_without_path_or_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "secret-name.json"
            corrupt = Path(temp_dir) / "corrupt.json"
            non_list = Path(temp_dir) / "non-list.json"
            corrupt.write_text("raw-secret-payload", encoding="utf-8")
            non_list.write_text("{}", encoding="utf-8")

            with patch.object(
                policy_conditions, "_LOAD_WARNING_EMITTED", False
            ), warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                self.assertEqual(load_support_conditions(missing), {})
                self.assertEqual(load_support_conditions(corrupt), {})
                self.assertEqual(load_support_conditions(non_list), {})

        self.assertEqual(len(captured), 1)
        message = str(captured[0].message)
        self.assertIn("프로필 후처리를 생략", message)
        self.assertNotIn("secret-name", message)
        self.assertNotIn("raw-secret-payload", message)

    def test_malformed_rows_warn_once_with_counts_only(self) -> None:
        degraded = _row("degraded", "JA0101")
        degraded["JA0201"] = "N"
        duplicate = _row("duplicate", "JA0101")
        payload = [
            _wrapper(degraded),
            _wrapper(duplicate),
            _wrapper(duplicate),
            _wrapper(_row("dropped"), match_count=0),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "conditions.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with patch.object(
                policy_conditions, "_LOAD_WARNING_EMITTED", False
            ), warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                loaded = load_support_conditions(path)

        self.assertEqual(set(loaded), {"degraded"})
        self.assertEqual(len(captured), 1)
        message = str(captured[0].message)
        self.assertIn("accepted=1", message)
        self.assertIn("dropped=1", message)
        self.assertIn("duplicates=1", message)
        self.assertIn("degraded_categories=1", message)
        self.assertNotIn("degraded\"", message)
        self.assertNotIn("duplicate\"", message)

    def test_invalid_value_or_missing_code_disables_only_that_category(self) -> None:
        invalid_value = _row("bad-value")
        invalid_value["JA0101"] = "N"
        missing_code = _row("missing-code")
        missing_code.pop("JA0328")
        missing_employment_code = _row("missing-employment-code")
        missing_employment_code.pop("JA0313")

        loaded = self._load(
            [
                _wrapper(invalid_value),
                _wrapper(missing_code),
                _wrapper(missing_employment_code),
                _wrapper(_row("valid", "JA0328")),
            ]
        )

        self.assertEqual(
            set(loaded),
            {"bad-value", "missing-code", "missing-employment-code", "valid"},
        )
        self.assertNotIn("JA0101", loaded["bad-value"])
        self.assertIn("JA0201", loaded["bad-value"])
        self.assertNotIn("JA0328", loaded["missing-code"])
        self.assertIn("JA0101", loaded["missing-code"])
        self.assertNotIn("JA0326", loaded["missing-employment-code"])
        self.assertIn("JA0101", loaded["missing-employment-code"])

    def test_captures_title_as_supplemental_non_code_key(self) -> None:
        """2026-09-11 추가: matches_profile의 제목 기반 보완 규칙(아래
        ProfileMatcherTests)이 참고할 수 있게 "서비스명"을 "_title" 키로
        얼어둔다. 이 키는 JA로 시작하지 않아 _active_codes/
        _category_matches가 순회하는 codes frozenset과 결코 겹치지 않아야 한다.
        """
        row = _row("titled")
        row["서비스명"] = "남성장애인 출산비용 지원"

        loaded = self._load([_wrapper(row)])

        self.assertEqual(loaded["titled"]["_title"], "남성장애인 출산비용 지원")
        for code in _CODES:
            self.assertIn(code, loaded["titled"])

    def test_zero_row_multi_row_and_duplicate_service_fail_open(self) -> None:
        loaded = self._load(
            [
                _wrapper(match_count=0, current_count=0),
                _wrapper(
                    _row("multi"),
                    _row("other"),
                    match_count=2,
                    current_count=2,
                ),
                _wrapper(_row("duplicate", "JA0101")),
                _wrapper(_row("duplicate", "JA0101")),
                _wrapper(_row("multi", "JA0102")),
                _wrapper(_row("valid", "JA0102")),
            ]
        )

        self.assertEqual(set(loaded), {"valid"})

    def test_noncanonical_service_id_is_not_trimmed_or_guessed(self) -> None:
        loaded = self._load(
            [
                _wrapper(_row(" service-1", "JA0101")),
                _wrapper({**_row("service-2"), "서비스ID": 2}),
            ]
        )

        self.assertEqual(loaded, {})

    def test_missing_or_mismatched_counts_drop_that_service(self) -> None:
        row = _row("service-1", "JA0101")
        loaded = self._load(
            [
                {"data": [row]},
                _wrapper(row, match_count=2),
                _wrapper(row, current_count=0),
            ]
        )

        self.assertEqual(loaded, {})


class ProfileMatcherTests(unittest.TestCase):
    def test_gender_codes_are_or_within_category(self) -> None:
        both = _row("service", "JA0101", "JA0102")

        self.assertTrue(matches_profile(both, _plan(gender="male")))
        self.assertTrue(matches_profile(both, _plan(gender="female")))
        self.assertFalse(
            matches_profile(_row("service", "JA0101"), _plan(gender="female"))
        )

    def test_income_uses_interval_overlap(self) -> None:
        expected = {
            "under_30": {"JA0201"},
            "pct_30_50": {"JA0201"},
            "pct_50_75": {"JA0201", "JA0202"},
            "pct_75_100": {"JA0202", "JA0203"},
            "pct_100_150": {"JA0203", "JA0204"},
            "over_150": {"JA0204", "JA0205"},
        }

        for slot, matching_codes in expected.items():
            for code in ("JA0201", "JA0202", "JA0203", "JA0204", "JA0205"):
                with self.subTest(slot=slot, code=code):
                    self.assertEqual(
                        matches_profile(
                            _row("service", code),
                            _plan(income_bracket=slot),
                        ),
                        code in matching_codes,
                    )

    def test_exact_employment_subset_only(self) -> None:
        employed = _row("service", "JA0326")
        job_seeking = _row("service", "JA0327")

        self.assertTrue(matches_profile(employed, _plan(employment_status="employed")))
        self.assertFalse(
            matches_profile(employed, _plan(employment_status="job_seeking"))
        )
        self.assertTrue(
            matches_profile(job_seeking, _plan(employment_status="job_seeking"))
        )
        # 2026-09-11 수정: 이 세 상태는 더 이상 "판단 불가"로 fail-open하지
        # 않는다 - 사용자가 분명히 밝힌 값이라 "재직자만" 정책에서는 제외돼야
        # 한다(_EMPLOYMENT_SLOT_CODES 주석 참고). 조건이 없는 정책까지 막지는
        # 않는다는 건 아래 test_categories_are_combined_with_and 등에서 계속
        # 확인한다.
        for excluded in ("self_employed", "student", "not_working"):
            with self.subTest(excluded=excluded):
                self.assertFalse(
                    matches_profile(employed, _plan(employment_status=excluded))
                )

    def test_unmapped_active_employment_code_prevents_exclusion(self) -> None:
        mixed = _row("service", "JA0326", "JA0313")

        self.assertTrue(
            matches_profile(mixed, _plan(employment_status="job_seeking"))
        )

    def test_disability_excludes_non_disabled_from_registered_only_policy(self) -> None:
        """2026-09-11 수정 전 이름은 test_disability_is_positive_registered_only
        였고, "not_registered"도 통과한다고(=버그) 못박아 검증하고 있었다. 실사용
        중 비장애인이라고 답했는데 장애인 전용 정책이 그대로 검색 결과에 뜨는 게
        확인돼 의도적으로 뒤집었다 - registered는 여전히 통과하고,
        not_registered는 이제 제외된다(_DISABILITY_SLOT_CODES 주석 참고).
        """
        registered_policy = _row("service", "JA0328")

        self.assertTrue(
            matches_profile(
                registered_policy, _plan(disability_status="registered")
            )
        )
        self.assertFalse(
            matches_profile(
                registered_policy, _plan(disability_status="not_registered")
            )
        )

    def test_disability_title_fallback_only_when_code_silent_and_confirmed(
        self,
    ) -> None:
        """2026-09-11 추가: JA0328이 전부 결측(즉 활성 코드 없음)이면
        위 테스트의 registered_policy와 달리 코드 기반 검사만으로는 걸러내지
        못한다(실사용 사례: "남성장애인 출산비용 지원" - JA0328이 전부
        None인데도 검색 결과에 뜼다). 이 보완 규칙은 title이 "장애인"을
        포함하고, 사용자가 비장애인이라고 확정 답변한 경우에만 개입하고,
        이미 적격으로 판단된 사람(registered, 또는 미확인)은 절대
        건드리지 않는다.
        """
        code_silent = _row("male-disability-benefit")
        code_silent["_title"] = "남성장애인 출산비용 지원"

        # 코드가 침묵하고 + 제목이 장애인을 명시하고 + 사용자가 비장애인이라고
        # 확정한 경우만 제외된다.
        self.assertFalse(
            matches_profile(code_silent, _plan(disability_status="not_registered"))
        )
        # 장애인 본인은 여전히 통과한다 - 이미 적격인 사람을 새로 끞워넣지
        # 않는다.
        self.assertTrue(
            matches_profile(code_silent, _plan(disability_status="registered"))
        )
        # 미확인(UNKNOWN, 즉 슬롯 미지정)은 건드리지 않는다 - 확정 답변이
        # 아니면 적격을 숨기지 않는다는 원칙.
        self.assertTrue(matches_profile(code_silent, _plan()))
        # 제목에 마커가 없으면(_title 없음) 보완 규칙이 개입하지 않는다
        # (이미 기존 fail-open 동작과 같다).
        self.assertTrue(
            matches_profile(
                _row("no-title"), _plan(disability_status="not_registered")
            )
        )

    def test_self_employed_title_fallback_only_when_code_silent_and_confirmed(
        self,
    ) -> None:
        """2026-09-11 추가: 장애인 버전과 같은 이유의 자영업자/프리랜서
        버전이다(실사용 사례: "1인 자영업자, 프리랜서 등 출산휴가급여").
        """
        code_silent = _row("freelancer-benefit")
        code_silent["_title"] = "1인 자영업자, 프리랜서 등 출산휴가급여"

        self.assertFalse(
            matches_profile(code_silent, _plan(employment_status="employed"))
        )
        # 자영업자 본인은 여전히 통과한다.
        self.assertTrue(
            matches_profile(code_silent, _plan(employment_status="self_employed"))
        )
        self.assertTrue(matches_profile(code_silent, _plan()))

    def test_categories_are_combined_with_and(self) -> None:
        values = _row("service", "JA0102", "JA0202", "JA0326", "JA0328")
        matching = _plan(
            gender="female",
            income_bracket="pct_50_75",
            employment_status="employed",
            disability_status="registered",
        )
        wrong_gender = _plan(
            gender="male",
            income_bracket="pct_50_75",
            employment_status="employed",
            disability_status="registered",
        )

        self.assertTrue(matches_profile(values, matching))
        self.assertFalse(matches_profile(values, wrong_gender))

    def test_invalid_category_fails_open_without_disabling_other_categories(self) -> None:
        invalid_gender = _row("service", "JA0101", "JA0205")
        invalid_gender.pop("JA0102")
        invalid_income = _row("service", "JA0102", "JA0205")
        invalid_income["JA0201"] = "N"
        missing_employment = _row("service", "JA0101", "JA0326")
        missing_employment.pop("JA0313")

        self.assertFalse(
            matches_profile(
                invalid_gender,
                _plan(gender="female", income_bracket="under_30"),
            )
        )
        self.assertTrue(
            matches_profile(
                invalid_income,
                _plan(gender="female", income_bracket="under_30"),
            )
        )
        self.assertFalse(
            matches_profile(
                missing_employment,
                _plan(gender="female", employment_status="job_seeking"),
            )
        )

    def test_missing_or_unknown_category_data_fails_open(self) -> None:
        values = _row("service", "JA0101")
        values.pop("JA0102")
        no_unmapped_codes = _row("service", "JA0326")
        for code in (
            "JA0313",
            "JA0314",
            "JA0315",
            "JA0316",
            "JA0317",
            "JA0318",
            "JA0319",
            "JA0320",
            "JA0322",
            "JA1101",
            "JA1102",
            "JA1103",
        ):
            no_unmapped_codes.pop(code)

        self.assertTrue(matches_profile(values, _plan(gender="female")))
        self.assertTrue(matches_profile(_row("service"), _plan(gender="female")))
        self.assertTrue(
            matches_profile(
                no_unmapped_codes, _plan(employment_status="job_seeking")
            )
        )


def _candidate(source_id: str) -> RetrievedChunk:
    text = f"정책 {source_id}"
    chunk = Chunk(
        schema_version=SCHEMA_VERSION,
        chunk_id=f"chunk-{source_id}",
        doc_id=f"subsidy:{source_id}:v1",
        source_type=SourceType.SUBSIDY,
        text=text,
        heading_path=("지원대상",),
        ordinal=0,
        citation_locator="지원대상",
        content_hash=compute_content_hash(text),
        metadata={"source_id": source_id},
    )
    return RetrievedChunk(
        query_id="q-user-type",
        chunk=chunk,
        rank=1,
        score=0.1,
        score_type="cosine_distance",
        retriever_version="test:fixture",
        index_name="subsidy",
    )


class EvaluateConditionsTests(unittest.TestCase):
    """2026-09-12 추가: N9(eligibility_verdict)가 "장애 여부, 성별, 소득 수준,
    취업 상태를 확인하지 못했다"를 실제로 확인 가능한 경우에도 무조건 보여주던
    문제를 고치기 위해 추가한 evaluate_conditions()를 검사한다. matches_profile과
    판단 기준이 같은지는 위 ProfileMatcherTests가 계속 검증한다.
    """

    def test_checked_true_and_violated_false_when_code_confirms_match(self) -> None:
        values = _row("service", "JA0102", "JA0202", "JA0326", "JA0328")
        plan = _plan(
            gender="female",
            income_bracket="pct_50_75",
            employment_status="employed",
            disability_status="registered",
        )

        result = evaluate_conditions(values, plan)

        for aspect in ("gender", "income_bracket", "employment_status", "disability_status"):
            with self.subTest(aspect=aspect):
                self.assertTrue(result[aspect]["checked"])
                self.assertFalse(result[aspect]["violated"])

    def test_checked_true_and_violated_true_when_code_confirms_mismatch(self) -> None:
        values = _row("service", "JA0101", "JA0328")  # 남성만, 장애인만
        plan = _plan(gender="female", disability_status="not_registered")

        result = evaluate_conditions(values, plan)

        self.assertTrue(result["gender"]["checked"])
        self.assertTrue(result["gender"]["violated"])
        self.assertTrue(result["disability_status"]["checked"])
        self.assertTrue(result["disability_status"]["violated"])

    def test_not_checked_when_code_silent_or_user_value_unknown(self) -> None:
        no_restriction = _row("service")  # 모든 JA 코드 None

        result = evaluate_conditions(no_restriction, _plan(gender="female"))
        self.assertFalse(result["gender"]["checked"])
        self.assertFalse(result["gender"]["violated"])

        # 코드는 있지만(성별 제한) 사용자 값을 모르면(미확인) 역시 대조 못함.
        gendered = _row("service", "JA0101")
        result2 = evaluate_conditions(gendered, _plan())
        self.assertFalse(result2["gender"]["checked"])
        self.assertFalse(result2["gender"]["violated"])

    def test_title_fallback_counts_as_checked_and_violated(self) -> None:
        code_silent = _row("service")
        code_silent["_title"] = "남성장애인 출산비용 지원"

        result = evaluate_conditions(
            code_silent, _plan(disability_status="not_registered")
        )

        self.assertTrue(result["disability_status"]["checked"])
        self.assertTrue(result["disability_status"]["violated"])

    def test_unmapped_active_employment_code_leaves_employment_unchecked(self) -> None:
        mixed = _row("service", "JA0326", "JA0313")

        result = evaluate_conditions(mixed, _plan(employment_status="job_seeking"))

        self.assertFalse(result["employment_status"]["checked"])
        self.assertFalse(result["employment_status"]["violated"])

    def test_matches_profile_agrees_with_evaluate_conditions(self) -> None:
        matching = _row("service", "JA0102", "JA0202", "JA0326", "JA0328")
        plan = _plan(
            gender="female",
            income_bracket="pct_50_75",
            employment_status="employed",
            disability_status="registered",
        )
        mismatched = _row("service", "JA0101")

        self.assertTrue(matches_profile(matching, plan))
        self.assertFalse(matches_profile(mismatched, _plan(gender="female")))


class PolicyUserTypeTests(unittest.TestCase):
    """2026-09-11 추가: 사용자구분("개인"/"법인/시설/단체" 등)는 JA 코드
    sidecar와 무관한 별도 축(처리된 subsidy jsonl에만 있다)이라 따로 검사된다.
    실사용 사례: "장애인고용장려금지원"(사용자구분=법인/시설/단체, JA 코드는
    모두 결측)이 개인 질문에 그대로 띄던 것을 보고 발견해 추가한 필터다.
    """

    def _load(self, docs: list[dict]) -> dict[str, frozenset[str]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "subsidy_documents.jsonl"
            path.write_text(
                "\n".join(json.dumps(doc, ensure_ascii=False) for doc in docs),
                encoding="utf-8",
            )
            return load_policy_user_types(path)

    def test_loads_pipe_separated_user_types_by_source_id(self) -> None:
        loaded = self._load(
            [
                {
                    "source_id": "org-only",
                    "title": "장애인고용장려금지원",
                    "metadata": {"user_type": "법인/시설/단체"},
                },
                {
                    "source_id": "individual-and-household",
                    "title": "첨만남이용권",
                    "metadata": {"user_type": "개인||가구"},
                },
                {"source_id": "no-metadata", "title": "x"},
            ]
        )

        self.assertEqual(loaded["org-only"], frozenset({"법인/시설/단체"}))
        self.assertEqual(
            loaded["individual-and-household"], frozenset({"개인", "가구"})
        )
        self.assertNotIn("no-metadata", loaded)

    def test_missing_file_fails_open(self) -> None:
        self.assertEqual(
            load_policy_user_types("/nonexistent/subsidy_documents.jsonl"), {}
        )

    def test_is_individual_applicable(self) -> None:
        is_ok = policy_conditions._is_individual_applicable
        self.assertTrue(is_ok(frozenset({"개인"})))
        self.assertTrue(is_ok(frozenset({"가구"})))
        self.assertTrue(is_ok(frozenset({"개인", "법인/시설/단체"})))
        self.assertFalse(is_ok(frozenset({"법인/시설/단체"})))
        self.assertFalse(is_ok(frozenset({"소상공인"})))
        # 값이 없으면(수집 못함) fail-open한다.
        self.assertTrue(is_ok(None))
        self.assertTrue(is_ok(frozenset()))

    def test_filter_candidates_excludes_org_only_policies_for_individual_users(
        self,
    ) -> None:
        candidates = [_candidate("org-only"), _candidate("individual")]
        user_types = {
            "org-only": frozenset({"법인/시설/단체"}),
            "individual": frozenset({"개인"}),
        }

        kept = filter_candidates(candidates, None, _plan(), user_types=user_types)

        self.assertEqual(
            [c.chunk.metadata["source_id"] for c in kept], ["individual"]
        )

    def test_filter_candidates_user_type_check_is_independent_of_conditions(
        self,
    ) -> None:
        """support_conditions sidecar가 아예 없어도(conditions=None) 사용자
        구분 필터는 독립적으로 동작해야 한다 - 이게 실제 버그였다
        ("장애인고용장려금지원"은 support_conditions=None이어도 개인
        사용자에게 그대로 띄다).
        """
        candidates = [_candidate("org-only")]
        user_types = {"org-only": frozenset({"법인/시설/단체"})}

        kept = filter_candidates(candidates, None, _plan(), user_types=user_types)

        self.assertEqual(kept, ())

    def test_filter_candidates_without_user_types_keeps_prior_behavior(self) -> None:
        """user_types 미지정(기본값 None)은 기존 호출부(policy_search.py)와
        기존 테스트들이 그대로 동작하게 보장해야 한다.
        """
        candidates = [_candidate("org-only")]

        kept = filter_candidates(candidates, None, _plan())

        self.assertEqual(
            [c.chunk.metadata["source_id"] for c in kept], ["org-only"]
        )


if __name__ == "__main__":
    unittest.main()
