"""목적 2(내용 원문 일치 확인) 검증용 실험 스크립트.

생성 모델이 원문에 없는 내용(금액 조작, 조건 조작)을 지어냈을 때, 같은 모델을
"검증자(judge)" 역할로 다시 호출해서 그 조작을 실제로 잡아내는지 확인한다.
LLM 자체를 파인튜닝하지 않고, 생성과 검증을 분리한 2단계 호출로 처리한다.

실행 전제: RunPod(A40) 등에서 아래처럼 모델을 vLLM으로 띄워둔 상태여야 함
    vllm serve <model> --max-model-len 16384 --port 8000
"""

from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")

MODEL = "skt/A.X-4.0-Light"  # 비교 대상: skt/A.X-4.0-Light, Qwen/Qwen3.5-9B, Bllossom/llama-3.2-Korean-Bllossom-3B

verify_schema = {
    "type": "object",
    "properties": {
        "일치_여부": {"type": "string", "enum": ["일치", "불일치"]},
        "불일치_근거": {"type": "string"},
    },
    "required": ["일치_여부", "불일치_근거"],
}

source_text = """
정책명: 영유아보육료 지원
지원대상: 만 0~5세 어린이집을 이용하는 영유아
지원형태: 보육료 바우처 월 최대 50만원
선정기준: 소득 무관, 어린이집 재원 아동 전원
"""

# 일부러 원문에 없는 내용(금액 조작 + 조건 조작)을 섞은 가짜 생성문
fake_generated = """
{
  "정책명": "영유아보육료 지원",
  "지원형태": "보육료 바우처 (월 최대 80만원)",
  "최종결과_문구": "소득 중위 200% 이하 가구의 만 0~5세 아동에게 월 최대 80만원을 지원합니다.",
  "배지_상태": "자격_충족"
}
"""

verify_prompt = f"""아래 [원문]과 [생성된 문장]을 비교해줘.
[생성된 문장]에 [원문]에 없는 정보(숫자, 조건, 사실)가 하나라도 들어가 있으면 "불일치"로 판정하고 그 부분을 구체적으로 적어줘.
[원문]에 있는 내용만으로 이루어져 있으면 "일치"로 판정해줘.

[원문]
{source_text}

[생성된 문장]
{fake_generated}
"""

response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": verify_prompt}],
    response_format={
        "type": "json_schema",
        "json_schema": {"name": "verify_result", "schema": verify_schema},
    },
)
print(response.choices[0].message.content)
