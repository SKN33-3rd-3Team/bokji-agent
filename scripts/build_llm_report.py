"""Render the local comparison report using preserved measurements and review notes."""
import json
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/llm_comparison_20260915'
DATA=json.loads((OUT/'result_summary.json').read_text(encoding='utf-8'))
NOTES=json.loads((OUT/'report_notes.json').read_text(encoding='utf-8'))
pdfmetrics.registerFont(TTFont('Malgun','C:/Windows/Fonts/malgun.ttf'))
pdfmetrics.registerFont(TTFont('MalgunBold','C:/Windows/Fonts/malgunbd.ttf'))
pdfmetrics.registerFontFamily('Malgun',normal='Malgun',bold='MalgunBold')
NAVY=colors.HexColor('#17365D'); GREY=colors.HexColor('#526277')
styles={
 'title':ParagraphStyle('title',fontName='MalgunBold',fontSize=23,leading=32,textColor=NAVY,spaceAfter=18,wordWrap='CJK'),
 'h1':ParagraphStyle('h1',fontName='MalgunBold',fontSize=17,leading=25,textColor=NAVY,spaceAfter=15,wordWrap='CJK'),
 'h2':ParagraphStyle('h2',fontName='MalgunBold',fontSize=11.5,leading=18,textColor=NAVY,spaceBefore=13,spaceAfter=7,wordWrap='CJK'),
 'body':ParagraphStyle('body',fontName='Malgun',fontSize=10,leading=16,spaceAfter=9,wordWrap='CJK'),
 'small':ParagraphStyle('small',fontName='Malgun',fontSize=8.5,leading=13,textColor=GREY,spaceAfter=7,wordWrap='CJK'),
 'cell':ParagraphStyle('cell',fontName='Malgun',fontSize=9,leading=14,wordWrap='CJK'),
 'head':ParagraphStyle('head',fontName='MalgunBold',fontSize=9,leading=14,textColor=colors.white,wordWrap='CJK'),
}
story=[]
def para(text,style='body'):
    return Paragraph(escape(str(text)).replace('\n','<br/>'),styles[style])
def add(text,style='body'):story.append(para(text,style))
def table(headers,rows,widths):
    grid=[[para(x,'head') for x in headers]]+[[para(x,'cell') for x in row] for row in rows]
    t=Table(grid,colWidths=widths,repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),
        ('LINEBELOW',(0,1),(-1,-1),.35,colors.HexColor('#DCE4ED')),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F4F7FA')])]))
    story.extend([t,Spacer(1,11)])
def page(title):
    if story:story.append(PageBreak())
    add(title,'h1')
def footer(canvas,doc):
    canvas.saveState(); w,h=A4
    canvas.setStrokeColor(colors.HexColor('#DCE4ED'));canvas.line(43,42,w-43,42)
    canvas.setFont('Malgun',8);canvas.setFillColor(GREY)
    canvas.drawString(43,28,'Bokji Agent | 로컬 LLM 비교 | 2026.09.15')
    canvas.drawRightString(w-43,28,str(doc.page));canvas.restoreState()

add('우리 서비스 답변에\n어떤 모델이 잘 맞을까?','title')
add('로컬 LLM 3종 비교 보고서','h2')
add('Qwen3.5-9B · A.X-4.0-Light · Bllossom-3B\n세 모델 모두 Q4_K_M 양자화 버전 / Ollama 로컬 실행','small')
for p in NOTES['conclusion']:add(p)
table(['평가 묶음','모델당 문항','무엇을 확인했나'],[
 ['팀 질문 세트','8개','질문 속 조건 이해와, 같은 확인 결과를 쉬운 안내문으로 쓰는 능력'],
 ['별도 품질 평가','45개','정책 질문 35개와 고정 안내문 상태 10개. 2개는 모델을 호출하지 않는 확인용'],
 ['전체 실행','53개 × 3종','총 159개 기록. 재시도와 자체 검사는 별도 호출로 보존']], [110,100,299])
add('이 결과의 범위','h2')
add('현재 저장소에 있는 공개 정책 8개를 사용했습니다. 전체 검색부터 지원자격 판단까지 이어지는 운영 서비스 시험은 아닙니다. 팀 질문 중 근거가 없는 내용은 사용자의 선택에 따라 확인 불가로 두었습니다.')
add('점수는 모델 이름을 가리고 각 답변을 AI가 1회 검토한 참고 점수입니다. 사람이나 전문가의 평가, 최신 정책의 사실 확인 결과로 해석하면 안 됩니다.','small')

page('1. 팀 질문 세트 결과')
add('팀원의 원본 질문 8개를 그대로 사용했습니다. 질문을 이해하는 단계와 안내문을 쓰는 단계를 따로 실행했습니다. 안내문에는 세 모델 모두 같은 확인 결과를 주었습니다.')
table(['모델','질문 이해 / 5점','안내문 표현 / 5점'],[[r[0],f'{r[1]:.2f}',f'{r[2]:.2f}'] for r in DATA['team_summary']],[185,162,162])
add('질문 6(근거 없는 금액·기간 요구)은 가중치 2, 나머지는 1입니다. 질문 2의 되묻기는 서비스의 고정 문구이므로 안내문 표현 평균에서 제외했습니다.','small')
for title,text in NOTES['team_notes']:
    add(title,'h2');add(text)
add('원본의 예상 판정과 이번 점수가 다른 이유','h2')
add('청년 주거·스마트팜 등에는 현재 자료로 확인할 근거가 없습니다. 팀 문서의 예상 판정을 정답처럼 적용하지 않았습니다. 질문 6도 새로운 정책 지식을 찾아내는 능력 대신, “확인 불가”라는 상태를 바꾸지 않고 전달하는지를 확인했습니다.')
add('질문별 점수와 이유, 입력 질문, 모델 원문, 서비스가 읽은 조건은 「팀_질문세트_비교결과.xlsx」에서 확인할 수 있습니다.','small')

page('2. 별도 품질 평가 결과')
add('정책의 금액·신청·대상을 묻는 29개 질문에, 잘못된 금액 유도·신청 예외·없는 법 조문 요구 등 6개를 더했습니다. 아래의 분모는 이 35개 질문입니다.')
models=list(DATA['models'].values())
table(['항목','Qwen','A.X','Bllossom'],[
 ['질문 핵심을 잘 전달한 최종 답변 / 35',*[str(m['counters']['helpful']) for m in models]],
 ['근거 없는 주장이 있는 최종 답변 / 35',*[str(m['counters']['unsupported']) for m in models]],
 ['답변 형식을 못 맞춰 안내로 대체 / 35',*[str(m['counters'].get('generation_failure',0)) for m in models]],
 ['원문 인용 검사에서 차단 / 35',*[str(m['counters'].get('quote_rejected',0)) for m in models]],
 ['기존 단순 검사 통과 / 29',*[str(m['metrics']['legacy_passed']) for m in models]],
 ['질문별 응답 시간 중간값 / 초',*[f"{m['metrics']['median_case_seconds']:.2f}" for m in models]],
 ],[254,85,85,85])
add('앞의 두 항목은 AI가 근거와 최종 답변을 읽어 판단했습니다. “근거 없는 주장”이 적더라도 답변을 계속 보류하면 “핵심 전달”은 낮아질 수 있습니다.','small')
for title,text in NOTES['independent_notes']:
    add(title,'h2');add(text)
add('고정 안내문 10개는 별도 확인','h2')
add('금액·부적격·미확인·중복수급 등의 상태를 유지하는지 확인했습니다. 모델이 쓰는 요약과 서비스가 붙이는 사실 문구를 구분했습니다. 모델을 호출하지 않는 2개 문항은 모델 품질 점수에서 제외했습니다.')
add('모델이 호출된 8개 안내문에서 근거 없는 최종 주장은 '+', '.join(f'{r[0]} {r[3]}개' for r in DATA['n13_summary'])+'였습니다. 숫자를 그대로 적어도 자격이 없는 사람에게 지급된다고 말하면 오류로 보았습니다.','small')

page('3. 실제 답변에서 드러난 차이')
for item in NOTES['examples']:
    block=[para(item['title'],'h2'),para(item['text'])]
    if item.get('quote'):block.append(para('실제 문구: '+item['quote'],'small'))
    story.append(KeepTogether(block))
add('다음 개선 순서','h2')
for i,text in enumerate(NOTES['next_steps'],1):add(f'{i}. {text}')

page('4. 실행 조건과 읽을 때 주의할 점')
table(['대상 원본','실행 파일의 출처','양자화 / GPU'],[
 ['Qwen/Qwen3.5-9B','Ollama qwen3.5:9b','Q4_K_M / CUDA 13'],
 ['skt/A.X-4.0-Light','Ghiwook의 A.X-4.0-Light GGUF 변환본','Q4_K_M / Vulkan'],
 ['Bllossom/llama-3.2-Korean-Bllossom-3B','Bllossom 제공자의 공식 GGUF','Q4_K_M / Vulkan']], [195,190,124])
add('이 PC에서의 조건','h2')
add('RTX 2070 SUPER 8GB에서 한 번에 모델 하나씩 실행했습니다. Ollama는 0.34.0입니다. 세 모델의 답변 생성에는 유료 추론 API를 사용하지 않았고 공개 모델 파일만 내려받았습니다. .env는 필요하지 않아 변경하지 않았습니다.')
add('세 모델에 같은 생성 설정을 적용했습니다. 입력 길이 설정 8192, 최대 출력 1024, 무작위성 0, seed 42입니다. Qwen의 별도 사고 출력은 껐습니다. 나머지 설정과 모델 식별값은 Excel과 실행 기록에 담았습니다.','small')
add('GPU 실행 오류 처리','h2')
add('초기 CUDA 방식에서 계산 오류가 발생했습니다. Qwen은 Flash Attention을 끄고 정상 완료했고, A.X는 Vulkan으로 바꿔 완료했습니다. Qwen을 Vulkan으로 실행하면 모델 계산이 CPU에 배치되어 그 시도는 중단했습니다. 세 모델의 GPU 방식이 달라 속도는 이 환경의 참고 수치로만 보세요.')
add('한계와 재현 자료','h2')
add('고정된 소수 질문을 각 모델에 한 번씩 실행했습니다. 질문 순서와 캐시의 영향이 있으며, 통계적으로 우수함을 입증한 결과는 아닙니다. Holdout과 전체 원천 문서를 사용하지 않았습니다. AI 검토 점수는 팀원이 원문을 보고 다시 확인할 수 있도록 이유를 남겼습니다.')
add('코드는 원격 main의 f4661da로 동기화한 뒤 로컬 평가 브랜치에서 작업했습니다. 관련 테스트 138개 통과. 기존 HF 오류 문구 테스트 2개는 변경 전에도 실패함을 확인했습니다. 기존 로컬 변경은 Git stash에 보존했습니다.','small')
add('모델 출처와 파일','h2')
for label,url in [
 ('Qwen 패키지','https://ollama.com/library/qwen3.5:9b'),
 ('A.X 변환본','https://huggingface.co/Ghiwook/A.X-4.0-Light-Q4_K_M-GGUF'),
 ('Bllossom 변환본','https://huggingface.co/Bllossom/llama-3.2-Korean-Bllossom-3B-gguf-Q4_K_M')]:
    story.append(Paragraph(f'<link href="{url}" color="#245D98">{escape(label)}: {escape(url)}</link>',styles['small']))
add('원시 답변·실패 시도·고정 입력·검토 기록: experiments/model_evaluation/local_20260915/\n최종 선택과 실행 방법: 위 폴더의 RUN_NOTES.md','small')
doc=SimpleDocTemplate(str(OUT/'LLM_답변품질_비교보고서.pdf'),pagesize=A4,
    rightMargin=43,leftMargin=43,topMargin=42,bottomMargin=57,
    title='Bokji Agent 로컬 LLM 답변 품질 비교',author='Codex')
doc.build(story,onFirstPage=footer,onLaterPages=footer)
print(OUT/'LLM_답변품질_비교보고서.pdf')
