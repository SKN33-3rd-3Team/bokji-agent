"""노드 함수를 한 곳에서 re-export한다.

"""

from .result_assembly import assemble_result  # N12 결과 조립

__all__ = [
    "assemble_result",
]
