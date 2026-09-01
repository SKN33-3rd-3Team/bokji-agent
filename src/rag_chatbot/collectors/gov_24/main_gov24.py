"""보조금24 수집 파이프라인을 한 번에 순서대로 실행한다.

목록조회(list) -> 상세조회(detail) -> 지원조건조회(conditions) -> 병합(merge)
-> Document 변환(to_document) 순서로 전부 실행한다.

--limit N 옵션을 주면 상세조회·지원조건조회·병합·Document 변환까지 전부
목록 앞에서부터 N건만 사용해서 빠르게 파이프라인 전체를 테스트할 수 있다.
목록조회 자체는 항상 전체를 가져온다 — 이후 단계가 어떤 서비스ID를 쓸지
정하는 기준이 되고, 페이지 단위라 그 자체는 오래 걸리지 않기 때문이다.

병합 단계도 같은 limit을 받아서 목록 앞 N건만 병합한다. 그렇지 않으면
상세조회는 N건만 했는데 병합은 전체 목록을 대상으로 해버려서, 나머지
(10,968 - N)건이 "조회 안 한 것"이 아니라 "원천에 값이 없는 것"으로 잘못
표시된다.

사용법:
    python -m rag_chatbot.collectors.gov_24             # 전체 실행
    python -m rag_chatbot.collectors.gov_24 --limit 50  # 앞 50건 테스트
    python -m rag_chatbot.collectors.gov_24 -n 50       # --limit과 동일
"""

import argparse
import time

from . import gov24, merge_gov24, to_document


def _step(title: str, func, *args, **kwargs):
    print(f"\n===== {title} 시작 =====", flush=True)
    start = time.time()
    result = func(*args, **kwargs)
    print(f"===== {title} 완료 ({time.time() - start:.1f}초) =====", flush=True)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "보조금24 수집 파이프라인(목록조회 -> 상세조회 -> 지원조건조회 -> "
            "병합 -> Document 변환)을 순서대로 한 번에 실행한다."
        )
    )
    parser.add_argument(
        "--limit",
        "-n",
        type=int,
        default=None,
        help=(
            "테스트 모드: 목록 앞에서부터 이 건수만큼만 상세조회/지원조건조회/"
            "병합/Document 변환을 수행한다. 지정하지 않으면 전체 건수를 실행한다."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit/-n 은 1 이상의 정수여야 합니다.")

    if args.limit is not None:
        print(f"[main] 테스트 모드: 앞에서부터 {args.limit}건만 사용합니다.", flush=True)

    _step("1. 목록조회(list)", gov24.run_list)
    _step("2. 상세조회(detail)", gov24.run_detail, args.limit)
    _step("3. 지원조건조회(conditions)", gov24.run_conditions, args.limit)
    _step("4. 병합(merge)", merge_gov24.run, args.limit)
    _step("5. Document 변환(to_document)", to_document.run)

    print("\n전체 파이프라인 완료.", flush=True)
    if args.limit is not None:
        print(
            "(테스트 모드였습니다 — 전체 데이터로 다시 실행하려면 --limit 없이 재실행하세요. "
            "전체 수집은 1페이지부터 새 .partial에 받아 검증 후 교체합니다.)",
            flush=True,
        )


if __name__ == "__main__":
    main()
