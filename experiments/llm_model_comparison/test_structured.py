"""목적 1(결과물 형식 일정하게) 검증용 실험 스크립트.

vLLM으로 로컬 서빙 중인 모델에 response_format(JSON 스키마)을 강제해서,
파인튜닝 없이도 정해진 형식(정책명/지원형태/최종결과_문구/배지_상태)으로
답이 나오는지 확인한다.

실행 전제: RunPod(A40) 등에서 아래처럼 모델을 vLLM으로 띄워둔 상태여야 함
    vllm serve <model> --max-model-len 16384 --port 8000
"""

from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")

MODEL = "skt/A.X-4.0-Light"  # 비교 대상: skt/A.X-4.0-Light, Qwen/Qwen3.5-9B, Bllossom/llama-3.2-Korean-Bllossom-3B

gen_schema = {
    "type": "object",
    "properties": {
        "정책명": {"type": "string"},
        "지원형태": {"type": "string"},
        "최종결과_문구": {"type": "string"},
        "배지_상태": {"type": "string", "enum": ["자격_충족", "확인_필요"]},
    },
    "required": ["정책명", "지원형태", "최종결과_문구", "배지_상태"],
}

source_text = """
정책명: 영유아보육료 지원
지원대상: 만 0~5세 어린이집을 이용하는 영유아
지원형태: 보육료 바우처 월 최대 50만원
선정기준: 소득 무관, 어린이집 재원 아동 전원
"""

response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": source_text + "\n위 정책 정보를 정리해서 답해줘."}],
    response_format={
        "type": "json_schema",
        "json_schema": {"name": "policy_summary", "schema": gen_schema},
    },
)
print(response.choices[0].message.content)
