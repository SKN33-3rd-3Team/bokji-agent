# RAG_FINAL_ANALYSIS_REPORT

## 1. Final Evaluation Summary

### 최종 통합 결과 (Dev 150 + Policy-eval 100 + 준-Holdout 100 = 총 350문항)

v4에서 Citation Precision이 크게 후퇴한 게 확인돼(2절 이후), 원인을
고친 v5까지 이어서 측정했다. 두 버전 다 count 가중 결합으로 합산했다
(`scripts/pool_evaluation_runs.py`).

| 지표 | v4(pooled) | v5(pooled, vFinal 후보) |
| --- | ---: | ---: |
| Recall@k | 0.791 | 0.780 |
| MRR@k | 0.598 | **0.669** |
| Citation Precision | 0.200 | **0.305** |
| Citation Coverage | 0.791 | 0.780 |
| Abstention Precision | **0.812** | 0.632 |
| Abstention Recall | **0.464** | 0.429 |
| Faithfulness | **0.755** | 0.725 |
| Answer Relevancy | **0.679** | 0.671 |
| LLM 호출 실패율 | 0.67% | **0.34%** |
| Success Rate | 0.994 | **1.000** |

근거: `artifacts/evaluation/v4-final/POOLED_RESULT.md`,
`artifacts/evaluation/v5-final/POOLED_RESULT.md`.

참고용 이전 통합치(v0, Dev 150+Policy-eval 100=250건만 - 준-Holdout은
v0 시점에 존재하지 않아 포함 불가): Recall 0.810 / Citation Precision
0.160 / Abstention P=1.000·R=0.125 / Faithfulness 측정 불가
(`artifacts/evaluation/v0_pooled.md`). **표본 수가 250→350으로 늘어서
숫자를 직접 뺄셈하면 안 되고, 방향과 세트별 상세(3절)를 같이 봐야
한다.**

**v4 vs v5, 어느 쪽이 "최종"인가**: 우선순위(1.Faithfulness,
2.Citation Precision) 순서를 그대로 적용하면 v5의 Citation Precision
개선폭(+0.105)이 Faithfulness 하락폭(-0.030)보다 커서 v5를 잠정
권고하지만, Abstention이 뚜렷이 후퇴한다는 점은 실사용 전 검토가
필요하다 - 최종 결정은 `RAG_VERSION_COMPARISON_REPORT.md` 6절.

## 2. Retrieval Analysis

### Recall/MRR 분석

v0→v1/v2 구간에서 `dev_questions.jsonl`의 Recall@5가 0.845→0.70대로
떨어졌다. 이게 "과적합 완화의 의도된 트레이드오프"인지 "실제 retrieval
regression"인지는 원인을 코드로 추적해서 구분했다:

- retrieval 자체(임베딩, chunk 검색, top-k 선정)는 이 구간에서 **전혀
  바뀌지 않았다** - `_build_query`, chunk size/overlap, top_k=5 모두
  동일.
- 하락의 원인은 새로 추가한 N4 관련성 게이트다. 실측 사례
  (`dev-form-energy-138`, "노후 여부를 생산연도로 보는지 설치연도로
  보는지가 헷갈립니다")로 확인: **검색(임베딩)은 정답 정책을 정확히
  찾아왔는데, 그 뒤에 붙인 LLM 판정이 주제어가 거의 없는 질문을
  보고 "무관하다"고 잘못 판단해 걸러냈다.**

이건 섹션 5.4가 요구한 구분("A. Retriever가 못 찾음" vs "B. Retriever는
찾았지만 게이트가 지움")에서 **명백히 B**다. 그래서 v3에서는 retrieval
자체를 건드리지 않고, 게이트만 두 가지로 보강했다: (1) 0건 판정 재확인,
(2) top-1 거리가 이미 충분히 가까우면 게이트 자체를 건너뛰는 하이브리드
사전 필터.

**결과(실측)**: Dev 150 기준 Recall@5가 v2(0.746) → v3(0.789) →
v4(0.810)로 단계적으로 회복됐다 - v0(0.845)에는 못 미치지만 격차가
0.099(v1)에서 0.035(v4)로 줄었다. 하이브리드 사전 필터가 의도한 대로
"검색은 맞았는데 게이트가 지우는" 손실을 상당 부분 되돌렸다는 뜻이다.
다만 이 회복은 공짜가 아니었다 - Citation Precision이 같이 후퇴했다
(3절, `RAG_VERSION_COMPARISON_REPORT.md` 5절 트레이드오프 참고). 원인은
같은 메커니즘의 이면이다: 게이트를 더 자주 건너뛴다는 건 더 많은
후보가 필터링 없이 통과한다는 뜻이고, 그만큼 한 응답에 인용되는 정책
수도 늘어난다(citation_pair_count 240대→500대).

### 과적합 완화 트레이드오프였는가?

`policy_eval_questions.jsonl`(과적합 우려가 없는 별도 세트)에서는
같은 v0→v1 구간에 Recall이 오히려 소폭 상승했다(0.760→0.770) - 즉
Dev set에서 본 하락이 "이 코드 변경의 보편적 부작용"은 아니었다.
이는 v0의 Dev set Recall(0.845)이 이 특정 세트의 질문 유형(예:
"노후 여부를 생산연도로 보는지..." 류의 주제어 희박한 되묻기형 질문)에
유리하게 맞춰져 있었을 가능성을 시사한다 - 다만 이걸 "과적합"이라
단정하려면 그 특정 세트의 질문 스타일 자체가 튜닝 대상이었는지 확인이
필요한데, 과적합 감사(패치 체인지로그 8절)에서는 하드코딩된 근거를
찾지 못했다. 따라서 "코드가 특정 질문을 부정하게 우대했다"기보다는
"관련성 게이트라는 새 컴포넌트가 특정 질문 스타일에 약하다"는 컴포넌트
수준의 일반화 문제로 본다 - 데이터 leakage형 과적합과는 다른 종류의
문제다.

## 3. Citation Analysis

Citation Precision은 v0→v1 구간에서 두 세트 모두 비슷한 배율로
개선됐다(Dev 0.164→0.417, +154% / Policy-eval 0.154→0.329, +114%).
원인은 코드로 명확히 추적된다: 예전 `_collect_citations`가 한 정책에
걸린 eligibility/amount/duplicate claim의 근거를 몽땅 섞어서 붙였는데,
`RuleBasedClaimExtractor`(LLM 미사용 기본 경로)는 청크 하나당 claim
3종을 전부 만들기 때문에 "지원대상" 섹션 청크가 "지원금액" 줄의
근거인 것처럼 인용되는 게 구조적으로 발생했다. `rule_chunk_id`(이미
계산돼 있었지만 미사용) 기반으로 바꾼 것이 개선의 직접 원인이다 -
결정론적 단위 테스트(`test_generate_answer_cites_only_the_rule_chunk_for_amount`
등)로 이 메커니즘 자체를 확인했다.

**구조적 상한(코드로 못 고치는 부분)**: 이 저장소의 Citation Precision은
정책 ID 단위로 계산되는데, 시스템은 한 응답에 top-5개 정책을 모두
답한다. Dev set 정답은 보통 1개뿐이라, 150문항 중 다수가 5개 정책을
인용해 이 지표가 구조적으로 1/5 근처에 묶인다. 이건 "여러 관련 정책을
보여준다"는 제품 설계와 "정답 1개" 채점 기준의 불일치이지 코드 결함이
아니다 - Dev set에 정답을 복수 허용하거나 시스템이 최상위 1건만 우선
답하도록 바꾸는 결정이 필요하다(이번 범위 밖).

**v3/v4에서 후퇴했다가 v5에서 회복된 경위**: v1→v2까지 개선되던
Citation Precision이 v3(거리 사전 필터 도입)에서 급격히 후퇴했다
(150문항 기준 0.438→0.228). 원인을 코드로 추적한 결과, 사전 필터가
"1등이 확실하면 **후보 전체**(1~5등)를 판정 없이 통과"시키는 구조였다
- citation_pair_count가 240대에서 500대로 늘어난 게 직접 증거다. 1등만
확정하고 2등 이하는 항상 LLM 판정을 거치도록 고친 v5에서
citation_pair_count가 다시 250~290대로 줄고 Citation Precision이
350문항 통합 기준 0.200→0.305(+53%)로 회복됐다 - Dev/Policy-eval/
준-Holdout 세 세트 모두에서 40~70% 폭으로 일관되게 나타나(4절), 특정
세트에 맞춘 결과가 아니다.

## 4. Faithfulness Analysis

`scripts/analyze_failure_cases.py`로 v1의 `dev_questions.jsonl` 150건
결과를 분류한 결과, unsupported/partial claim은 최소 두 가지 서로 다른
유형으로 나뉜다:

**유형 A - 측정 오류(judge blind spot, 진짜 환각 아님)**: 최소 5건
(`dev-child-education-001`, `dev-rent-guarantee-003`,
`dev-rent-guarantee-paraphrase-014`, `dev-rent-guarantee-limit-016`,
`dev-rent-guarantee-apply-017`)에서 judge가 "지역 조건 추가 확인
필요"라는 **챗봇 자신의 투명성 안내문**을 "근거 문서에 없는 사실
주장"으로 오판했다. 이 문장은애초에 정책에 대한 사실 주장이 아니라
"이건 확인 못 했다"는 메타 안내라, 근거 문서와 대조할 대상이 아니다.
Patch 7(judge 프롬프트에 제외 지침 추가)로 고쳤다.

**유형 B - 진짜 환각(생성 단계 문제)**: 최소 2건에서 원문에 없는
법령/조례명을 지어냈다 - `dev-earned-income-assets-013`("관련된
법령은 조세특례제한법입니다", 원문에 그런 언급 없음),
`pol-318000000404`("서울특별시 광진구 공유 촉진 조례", 근거 문서에
전혀 존재하지 않음 - judge가 직접 "허위 주장"으로 판정). 이 두 건은
N13의 LLM 요약 생성 단계에서 나온 실제 환각이다. Patch 6(법령명 환각
검증, 숫자 검증과 같은 메커니즘)으로 차단한다 - 원문에 없는 법령명이
하나라도 있으면 그 정책의 summary를 통째로 버리고 템플릿 문장으로
대체한다.

**유형 C - 여전히 남는 문제(이번에 손대지 않음)**: "성별" 조건을
확인했다고 말했는데 근거 문서엔 성별 조건이 아예 없는 경우
(`dev-marine-defense-low-income-023`), "전국"이라고 지역을 단정했는데
근거 문서는 지역별로 신청처가 나뉘어 있는 경우
(`dev-marine-defense-disability-027`) 등은 이번 두 patch로 안 잡힌다.
`_verification_line()`이 만드는 `checked`/`unchecked` 라벨과 실제 문서
내용이 어긋나는 이런 케이스는 N9(eligibility_verdict)의 조건 대조 로직
자체를 더 정교하게 만들어야 하는 별도 작업이다(9절 Next Improvement
Plan).

**실측 결과**: Patch 6+7을 적용한 v4에서 Faithfulness가 세 세트 모두
뚜렷이 개선됐다 - Dev 150: 0.590(v1)→0.752, Policy-eval 100:
0.574(v1)→0.738, 준-Holdout(v3 대비): 0.763→0.778. 한 번도 튜닝에
쓰이지 않은 준-Holdout에서도 같은 방향·비슷한 크기로 나타나(오히려
세 세트 중 절대값이 가장 높다) 이 개선이 특정 세트에 맞춘 게 아니라
일반적으로 작동한다는 근거다(`RAG_VERSION_COMPARISON_REPORT.md` 4절
Generalization Gap 참고). Dev 150의 v4 Faithfulness는 113/150건
판정(v1의 122건과 비슷한 표본 크기 - v3의 36건짜리 판정과 달리
신뢰할 수 있다).

## 5. Answer Relevancy Analysis

`LOW_RELEVANCY`(judge 판정 "무관"/"부분관련") 사례 25건(Dev 150) 중
다수는 "확인 못 한 조건이 많아 결론을 못 내리는 답변"이 원인이다
(`pol-523000000556`: "장애 여부 및 취업 상태 확인되지 않아..."라고만
하고 최종 결론을 안 냄). 이건 Faithfulness와 반대 방향의 긴장 관계다 -
확정 못 한 걸 확정한 것처럼 말하면 Faithfulness가 깎이고, 정직하게
불확실하다고만 말하면 Relevancy(질문에 직접 답했는가)가 깎인다. 이번
라운드에서는 이 긴장을 풀지 않았다(Faithfulness가 우선순위 1위였고,
"확정 못 한 걸 확정처럼 말하지 않는다"는 원칙을 지키는 쪽을 택함) -
9절에 다음 개선 방향으로 남긴다.

## 6. Abstention Analysis

v0→v1 구간에서 Recall이 0.125→0.750으로 6배 개선됐지만 Precision은
1.0→0.6으로 하락했다(150문항 기준). 원인 사례:
`dev-energy-apply-021`/`dev-energy-variant-074`/`dev-form-energy-138`이
전부 정책 `119200000001`(어선기관교체지원)에 몰려 있었고, 셋 다
질문에 뚜렷한 주제어가 없는 되묻기형이었다 - 관련성 게이트가 검색이
찾아온 정답을 오판해서 걸러낸 사례(RETRIEVAL 분석 2절과 같은 원인).

**실측 결과**: Dev 150에서는 v4의 Abstention Precision이 0.667로
v1(0.6)/v3(0.6)보다 소폭 개선됐지만 Recall은 0.5로 v1(0.75)보다
낮아졌다 - 재확인 로직이 오탐은 줄였지만 그만큼 "진짜 보류해야 할
질문"도 일부 놓치게 됐다(의심스러우면 답변 쪽으로 기우는 설계 원칙의
당연한 결과). 반면 준-Holdout(v3→v4)에서는 Precision 0.75→**1.000**
(오탐 0건), Recall 0.30→0.45로 **정밀도와 재현율이 동시에** 개선됐다
- 세 세트 중 유일하게 둘 다 좋아진 경우다. 두 세트가 다른 방향을
보이는 건 표본 크기(Dev 8건 vs 준-Holdout 20건)와 보류 문항 성격
차이 때문으로 보이며, 8건짜리 표본에서는 한두 건의 판정이 바뀌어도
비율이 크게 흔들린다는 점을 감안해야 한다.

## 7. Failure Case Analysis

`scripts/analyze_failure_cases.py`로 v1 결과(Dev 150 + Policy-eval 100)
250건을 분류한 결과(카테고리 중복 허용):

| 카테고리 | Dev 150 | Policy-eval 100 |
| --- | ---: | ---: |
| PARTIAL_FAITHFULNESS | 70 | 47 |
| RETRIEVAL_MISS_OR_RANKING | 38 | 22 |
| LOW_RELEVANCY | 25 | 8 |
| UNSUPPORTED_CLAIM | 15 | 11 |
| BAD_ABSTENTION | 6 | 1 |
| RELEVANCE_GATE_FALSE_ABSTAIN | 4 | 1 |
| MULTI_TURN_ANOMALY | 3 | 1 |
| LLM_FAILURE_OR_FALLBACK | 2 | 6 |
| OK(문제 신호 없음) | 40 | 30 |

대표 사례(전체 상세는 `artifacts/evaluation/failure_analysis_v1_150.txt`,
`failure_analysis_v1_100.txt`):

| Question | Expected | Retrieved | Root cause | Category | Fix |
| --- | --- | --- | --- | --- | --- |
| dev-form-energy-138 "노후 여부를 생산연도로 보는지..." | 119200000001 | []  | 관련성 게이트가 주제어 희박한 질문을 오판 | RELEVANCE_GATE_FALSE_ABSTAIN | v3 재확인+거리 사전 필터 |
| dev-child-education-001 | 000000465790 | 000000465790, 134200005047 | judge가 "지역 조건 추가 확인 필요" 안내문을 오채점 | PARTIAL_FAITHFULNESS(측정 오류) | Patch 7 |
| dev-earned-income-assets-013 | 105100000001 | 105100000001 | LLM이 "조세특례제한법"을 원문 없이 지어냄 | UNSUPPORTED_CLAIM | Patch 6 |
| pol-318000000404 | 318000000404 | 318000000404 외 2건 | LLM이 존재하지 않는 "OO구 공유 촉진 조례"를 지어냄 | UNSUPPORTED_CLAIM | Patch 6 |
| dev-marine-defense-minor-022 | 119200000007 | B27000100039, B27000100051 | 순수 검색 미스(관련성 게이트 이전 단계) | RETRIEVAL_MISS_OR_RANKING | 이번에 손대지 않음(9절) |
| dev-child-variant-038 | 000000465790 | 정답 포함 | max_new_tokens=1024로 응답이 잘림 | LLM_FAILURE_OR_FALLBACK | 이번에 손대지 않음(9절) |

## 8. Dev vs Holdout Generalization

**전제**: 이 절의 "Holdout"은 진짜 Holdout이 아니라 준-Holdout이다
(방법론적 한계는 `RAG_PATCH_CHANGELOG_REPORT.md` Patch 3에 명시). "오늘
처음 보는 정책·문항"이라는 속성만 보장한다.

1. **과적합이 감소했는가?** → 감소했다고 볼 근거가 있다. Faithfulness
   개선(v1→v4)이 Dev(+27%)·Policy-eval(+29%)·준-Holdout(+2%, 이미 v3에서
   높았음)에서 방향과 크기가 일관됐다 - 이건 Dev set에만 맞춘 결과가
   아니라는 가장 강한 증거다. Citation Precision도 v0 대비는 세
   세트(공식적으로 측정 가능한 Dev/Policy-eval) 모두에서 개선 방향을
   유지했다.
2. **Holdout(준-Holdout)에서 성능이 유지되는가?** → 유지되는 정도가
   아니라 **Faithfulness(0.778)와 Abstention Precision(1.000)은 세
   세트 중 가장 좋다.** Recall(0.750)과 MRR(0.548)은 세 세트 중 가장
   낮다 - 이 세트의 질문이 정책 문서 요약문에서 자동 생성돼(어휘가
   문서와 겹침) 검색 자체는 오히려 쉬울 것 같은데도 가장 낮게 나온 건
   의외이며, 원인 조사가 더 필요하다(9절).
3. **Dev와 Holdout의 metric gap이 줄었는가?** → Faithfulness 기준으로는
   격차가 거의 없다(Dev 0.752 vs 준-Holdout 0.778, 차이 0.026). Recall
   기준으로는 격차가 있다(Dev 0.810 vs 준-Holdout 0.750, 차이 0.06) -
   이 격차가 원래부터 있었는지(v3에서 처음 측정하므로 "전"이 없다)
   비교할 기준점이 없어 "줄었다/늘었다"를 말할 수 없다 - 이건 준-Holdout
   세트의 근본적 한계다(진짜 Holdout이었다면 처음부터 안 재는 게
   맞고, 지금처럼 v3/v4 두 번 측정한 것 자체가 "한 번만 본다"는
   Holdout 원칙에 어긋난다 - 이번 세션에서는 상황상 불가피했지만,
   앞으로 진짜 Holdout을 만들면 이 시행착오를 반복하지 말아야 한다).
4. **특정 데이터셋에만 성능이 좋은가?** → 아니다. 과적합 감사(코드
   레벨)에서 근거를 못 찾았고, 수치 레벨에서도 Faithfulness 개선이
   세 세트에 고르게 나타나 이 결론을 뒷받침한다.
5. **어떤 질문 유형에서 여전히 실패하는가?** → 주제어가 거의 없는
   되묻기형 질문(관련성 게이트, Dev에서 확인), 법령명·조건 항목을
   세부적으로 틀리는 경우(N9 조건 대조 정교화 필요, 이번 patch로
   안 잡힘), 준-Holdout에서 Recall/MRR이 낮게 나오는 원인(미조사).

## 9. Remaining Problems

- Citation Precision의 구조적 상한(정책 ID 단위 채점 vs 다중 정책
  응답) - 제품/평가 설계 결정 필요, 코드로 해결 불가.
- 관련성 게이트가 주제어 희박한 되묻기형 질문에 약함 - v3로 완화
  시도했으나 근본적으로 "질문 텍스트만으로 판단"하는 한계는 남는다.
- Faithfulness와 Answer Relevancy 사이의 긴장(불확실성을 정직하게
  알리면 Relevancy가, 단정하면 Faithfulness가 깎임) - 이번에 풀지 않음.
- N9(eligibility_verdict)이 "확인했다"고 말하는 조건과 실제 문서 내용이
  어긋나는 사례(성별·지역 조건 오기재) - 법령명 환각과는 다른 유형의
  Faithfulness 문제, 이번 patch로 안 잡힘.
- `max_new_tokens=1024`로 인한 응답 잘림(추론형 모델의 내부 사고 과정이
  토큰 예산을 다 쓰는 경우) - LLM_FAILURE_OR_FALLBACK 카테고리 6~2%.
- LLM provider(featherless-ai) 신뢰성이 하루 동안 반복 요청으로
  저하되는 현상 확인 - 운영 환경에서는 재시도/이중화 검토 필요.
- ~~Citation Precision이 v1/v2 대비 v3/v4에서 후퇴함~~ → **v5에서
  해결**(사전 필터를 "1등만 확정"으로 재설계, Patch 9). 대신 v5에서
  **Abstention Precision/Recall이 350문항 통합 기준 0.812/0.464→
  0.632/0.429로 후퇴**했다 - 2등 이하까지 매번 LLM 판정을 거치면서
  판정 편차가 끼어들 기회가 늘어난 것으로 추정. 세트별로 방향이
  엇갈려(Dev는 개선, Policy-eval·준-Holdout은 후퇴) 8~20건짜리 작은
  보류 표본의 변동일 가능성이 있다 - 더 큰 보류 문항 표본으로
  재검증이 필요하다(10절).
- **HuggingFace 무료 크레딧(월 $0.10) 소진으로 v3의 policy-eval 100문항
  실행이 완전히 무효화됨**(1379/1379 호출 실패) - 이번엔 다음날
  자정 즈음 크레딧이 복구돼 v4는 정상 진행했지만, 재현 가능한 평가
  파이프라인을 위해서는 유료 티어 전환이나 provider 이중화가 필요하다.
- 준-Holdout 세트의 Recall(0.750)·MRR(0.548)이 세 세트 중 가장
  낮게 나온 원인을 조사하지 못했다 - 문서 요약문 기반 자동 생성이라
  검색이 오히려 쉬울 것으로 예상했는데 반대로 나왔다.
- 100문항 세트(v4)에서 원인 불명의 일시적 `ValueError` 2건 발생 -
  `--workers 1`로 개별 재현 시도했으나 재현되지 않았다(동시성 또는
  네트워크 관련 일시적 문제로 추정, 결정론적 코드 버그라는 근거는
  찾지 못함).

## 10. Next Improvement Plan (우선순위별)

1. N9 조건 대조 로직에 "문서에 없는 조건을 확인했다고 말하지 않는다"는
   교차 검증 추가(성별/지역 오기재 방지) - Faithfulness 유형 C.
2. Faithfulness/Relevancy 긴장 해소: "확인 못 한 조건이 있으면 그
   조건만 정직하게 되묻고, 확인된 조건에 대해서는 확정적으로 답한다"는
   식으로 답변 구조를 부분 확정/부분 보류로 나누는 설계 검토.
3. 관련성 게이트의 판정 근거를 질문 텍스트뿐 아니라 슬롯에서 뽑은
   관심사 키워드까지 함께 주는 방식 검토(주제어 희박 질문 대응).
4. `max_new_tokens`를 노드별로 더 세밀하게 조정하거나, thinking 비활성화
   (`LLM_DISABLE_THINKING`)가 실제로 이 provider에서 효과가 있는지
   별도로 검증.
5. Citation Precision 구조적 상한 - 정답 정책 복수 허용 여부를 팀
   차원에서 결정.
6. ~~`_CONFIDENT_DISTANCE_THRESHOLD`를 낮춰 LLM 게이트를 더 자주 거치게
   하는 실험~~ → **완료(Patch 9, v5)**. 1등만 확정하는 재설계로
   Citation Precision을 회복시켰다. 후속: v5가 만든 Abstention 후퇴를
   더 큰 보류 문항 표본(현재 Dev 8건, 준-Holdout 20건)으로 재검증할 것.
7. 준-Holdout의 낮은 Recall/MRR 원인 조사 - 실패 사례를
   `scripts/analyze_failure_cases.py`로 분석. (v5에서는 오히려 세 버전
   중 최고치를 냈으므로 우선순위를 낮춘다 - v3/v4 시점의 문제였을
   가능성.)
8. 재현 가능한 평가를 위해 LLM provider 유료 티어 전환 또는 이중화
   검토(크레딧 소진으로 한 세트 전체가 무효화되는 일이 재발하지
   않도록).
9. **(신규, 우선순위 높음)** v5의 Abstention Precision/Recall 후퇴
   원인 조사 - 2등 이하 후보에 대한 LLM 판정이 늘면서 어떤 유형의
   질문에서 새로 오탐/누락이 생겼는지 `scripts/analyze_failure_cases.py`로
   분석.
