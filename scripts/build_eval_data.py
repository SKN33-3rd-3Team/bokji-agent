"""Create reader-facing tables from preserved outputs and blind AI reviews."""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.collect_local_eval import BASE, RUNS, read, lines, save, opaque
from scripts.eval_local_ollama import followup_metrics, summary

NAMES = {'qwen': 'Qwen3.5-9B', 'ax': 'A.X-4.0-Light', 'bllossom': 'Bllossom-3B'}
REASONS = {'answer':'답변 전달', 'model_abstained':'모델이 답변 보류',
    'generation_failure':'답변 형식 실패', 'quote_rejected':'원문 인용 검사 실패',
    'self_verifier_rejected':'모델 자체 검사에서 차단', 'verifier_failure':'자체 검사 응답 실패'}
REVIEW = BASE/'review'
OUT = ROOT/'output/llm_comparison_20260915'

def rounded(value):
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

def plain_note(text):
    for old,new in [('raw', '초안'), ('N13', '안내문'), ('policy-a','정책 A'), ('policy-b','정책 B')]:
        text=text.replace(old,new)
    return text

def split_text(text, limit=400):
    text = text if isinstance(text, str) else json.dumps(text,ensure_ascii=False,indent=2)
    # Plain contiguous slices preserve the exact text when joined.
    parts=[]
    start=0
    while start<len(text):
        end=min(start+limit,len(text))
        breaks=[i for i in range(start,end) if text[i]=='\n']
        if len(breaks)>=12: end=breaks[11]+1
        parts.append(text[start:end]);start=end
    return parts or ['']

def sheet(name, headers, rows, widths, note='', formulas=None):
    return {'name':name,'title':name,'note':note,'headers':headers,'rows':rows,
            'widths':widths,'formulas':formulas or []}

def get_reviews(files, expected):
    result={}
    for f in files:
        for row in read(REVIEW/f):
            key=row['blind_id']
            if key in result: raise ValueError('Duplicate review '+key)
            result[key]=row
    assert len(result)==expected,(len(result),expected)
    return result

def main():
    ai=get_reviews(['independent_review_a.json','independent_review_b.json'],129)
    tai=get_reviews(['team_review.json'],24)
    simple=get_reviews(['team_review_plain.json'],24)
    assert set(simple)==set(tai)
    for key,r in simple.items():
        assert (r['slots_score'],r['expression_score'])==(tai[key]['slots_score'],tai[key]['expression_score'])
    tai=simple
    indcases=read(BASE/'independent_final/cases.json')
    tcases=read(BASE/'team_final/cases.json')
    indmetrics,inddetail,indraw,n13metrics=[],[],[],[]
    teamscores,teamanswers,teamsummary=[],[],[]
    modeldata={}
    teamformula=[]
    conditions=[]
    extras=[]
    for index,(model,name) in enumerate(NAMES.items()):
        folder=BASE/RUNS[model]
        rows=lines(folder/f'{model}.jsonl')
        trows=lines(BASE/'team_final'/f'{model}.jsonl')
        assert len(rows)==45 and len(trows)==8
        meta=read(folder/f'{model}_metadata.json')
        metrics=summary(rows)
        counters=Counter()
        reviews={}
        for case,row in zip(indcases,rows):
            assert case['id']==row['id']
            isfollow=row['suite']!='n13'
            if isfollow:
                row.update(followup_metrics(case,row['final'],row['calls']))
                counters[row['outcome_reason']]+=1
            review=None if row.get('control') else ai[opaque('independent',model,row['id'])]
            if review:
                reviews[row['id']]=review
                if isfollow:
                    counters['helpful']+=review['final_helpful']
                    counters['unsupported']+=review['final_unsupported']
                    counters['raw_unfaithful']+=review['raw_faithful'] is False
                    counters['raw_assessed']+=review['raw_faithful'] is not None
            text=row['final'].get('text',row['final'].get('draft_answer',''))
            raw=row.get('generation')
            if row.get('control'):
                result='규칙 답변 / 모델 미호출'
            elif isfollow:
                result=REASONS[row['outcome_reason']]
            else:
                result=f"요약 채택 {len(row['summaries_accepted'])}/{row['policy_count']}"
            indetail=[name,row['id'],case['question'],result,
                '제외' if review is None else ('예' if review['final_helpful'] else '아니요'),
                '제외' if review is None else ('있음' if review['final_unsupported'] else '없음'),
                round(row['elapsed_s'],2),plain_note(review['note'] if review else case['review_note'])]
            inddetail.append(indetail)
            def add(kind,value):
                for part,chunk in enumerate(split_text(value),1):
                    indraw.append([name,row['id'],kind,part,chunk])
            add('최종 답변',text)
            for callidx,call in enumerate(row['calls'],1):
                add(f'모델 호출 {callidx} / '+('출력 한도 도달' if call.get('done_reason')=='length' else '정상 종료'),call.get('raw',call.get('error','')))
            if row.get('baseline'): add('모델 없이 만든 공통 답변',row['baseline']['draft_answer'])
            if row['suite']=='extra_followup':
                extras.append({'model':name,'id':row['id'],'question':row['question'],
                    'outcome':result,'final':text,'review':review})
        indmetrics.append([name,counters['helpful'],counters['unsupported'],counters['generation_failure'],
                           counters['quote_rejected'],metrics['legacy_passed'],round(metrics['median_case_seconds'],2)])
        nreviews=[r for cid,r in reviews.items() if cid.startswith('n13_')]
        n13metrics.append([name,metrics['n13_accepted_summaries'],
                           sum(r['final_helpful'] for r in nreviews),
                           sum(r['final_unsupported'] for r in nreviews),2])
        scored=[]
        for case,row in zip(tcases,trows):
            assert case['id']==row['id']
            r=tai[opaque('team',model,row['id'])]
            assert 1<=r['slots_score']<=5
            assert (r['expression_score'] is None)==(row['id']==2)
            if r['expression_score'] is not None: assert 1<=r['expression_score']<=5
            weight=case['review_weight']
            teamscores.append([name,row['id'],weight,r['slots_score'],r['expression_score'],r['slots_note'],r['expression_note']])
            scored.append((weight,r))
            def tadd(kind,value):
                for part,chunk in enumerate(split_text(value),1):
                    teamanswers.append([name,row['id'],kind,part,chunk])
            tadd('사용자 질문',case['question'])
            tadd('서비스가 읽은 조건',row['n1']['slots'])
            for n in ('n1','n13'):
                for callidx,call in enumerate(row[n]['calls'],1):
                    tadd(('질문 이해' if n=='n1' else '안내문 생성')+f' 원문 {callidx}',call.get('raw',call.get('error','')))
            final=row['n13']['final']
            tadd('최종 답변 / 고정 상태 기준',final.get('draft_answer',str(final)) if isinstance(final,dict) else final)
        slots=sum(w*r['slots_score'] for w,r in scored)/9
        expression=sum(w*r['expression_score'] for w,r in scored if r['expression_score'] is not None)/8
        tcalls=[c for r in trows for n in ('n1','n13') for c in r[n]['calls']]
        teamsummary.append([name,rounded(slots),rounded(expression),sum('error'in c for c in tcalls),
                            sum(not c.get('strict_json',False) for c in tcalls)])
        start=5+8*index;end=start+7
        teamformula.extend([
            {'cell':f'B{5+index}','formula':f"=SUMPRODUCT('문항별평가'!C{start}:C{end},'문항별평가'!D{start}:D{end})/SUM('문항별평가'!C{start}:C{end})"},
            {'cell':f'C{5+index}','formula':f"=SUMPRODUCT('문항별평가'!C{start}:C{end},'문항별평가'!E{start}:E{end})/(SUM('문항별평가'!C{start}:C{end})-'문항별평가'!C{start+1})"}])
        modeldata[model]={'name':name,'metrics':metrics,'counters':dict(counters),
            'team_slots':slots,'team_expression':expression,'team_reviews':{str(row['id']):tai[opaque('team',model,row['id'])] for row in trows},
            'independent_reviews':reviews,'source':str(folder.relative_to(ROOT)),
            'digest':meta['tags'][0]['digest'],
            'api_reported_gpu_size_bytes':meta['running_models_after_warmup'][0]['size_vram']}
        conditions.append([name,'Q4_K_M','CUDA 13' if model=='qwen' else 'Vulkan',
            '확인 (34/34층)' if model=='qwen' else '확인 (29/29층)',meta['tags'][0]['digest']])
    common='Ollama 0.34.0 / RTX 2070 SUPER / Q4_K_M / 같은 질문·근거·생성 설정. GPU 모듈이 달라 속도는 참고용.'
    source_rows=[]
    for case in tcases:
        source_rows.append([case['id'],case['question'],case['original'].get('선정 이유(무엇을 검증하는가)',''),case['team_expected_reference_only'],case['review_expectation']])
    policy_rows=[]
    policies=read(ROOT/'data/evaluation/light_followup_policies.json')
    for pid,p in policies.items():
        for part,chunk in enumerate(split_text(p),1): policy_rows.append([f'정책 {pid}',part,chunk])
    rubric=read(BASE/'team_final/rubric.json')
    rubricrows=[[k,v] for k,v in rubric['scale'].items()]+[
        ['가중치','질문 6은 2배, 나머지는 1배. 질문 2의 안내문 표현은 규칙 문구라 제외.'],
        ['평가자','모델명을 가리고 각 답변을 AI가 1회 검토. 실제 사람이나 전문가 점수가 아님.'],
        ['JSON 형식','JSON 외 문구가 있어도 서비스가 읽는 경우가 있음. 예: JSON을 코드 블록에 넣은 답변. 형식 불일치와 실제 처리 실패는 다름.'],
        ['Ollama','원본 비교표의 Ollama는 네 번째 모델이 아니라 세 모델의 공통 실행 도구로 정리.'],
        ['해석','질문 이해와 안내문 표현은 별도 점수. 실제 자격 판정이나 전체 검색 성능 점수가 아님.'],
        ['자료 부족','청년 주거·스마트팜 등 근거가 없는 주제는 확인 불가. 팀의 예상 판정은 참고만 함.'],
        ['고정 안내문','질문에서 읽은 조건을 안내문에 연결하지 않고, 미리 정한 같은 상태로 표현만 비교.']]
    definitions=[
        ['핵심 전달 / 35','정책 후속질문 29개 + 추가질문 6개. 근거로 답하거나 적절히 보류했는지 AI가 검토.'],
        ['근거 없는 최종 주장 / 35','사용자에게 전달한 문장 중 근거에 없거나 틀린 실질 주장. 단순 보류는 포함하지 않음.'],
        ['답변 형식 실패 / 35','서비스가 읽을 수 있는 JSON 구조를 만들지 못해 안내로 대체된 문항 수.'],
        ['원문 인용 실패 / 35','모델이 쓴 근거 문장이 원문과 일치하지 않아 답변이 차단된 문항 수. 내용이 맞아도 생길 수 있음.'],
        ['기존 기준 통과 / 29','기존 코드의 답변종류+일부 문구 포함 검사. 내용 전체의 사실 정확도 점수가 아님.'],
        ['응답시간','모델 준비 시간을 뺀 43개 생성 문항의 중간값. 재시도·자체 검사 포함, 1회 관측.'],
        ['안내문 상태 10','고정한 금액·자격 등으로 안내문 생성. 2개는 모델 미호출 확인용이며 품질 채점 제외.'],
        ['공통 답변 주의','공격 문구가 근거에 있으면 공통 템플릿에 그대로 노출됨. 모델이 명령을 실행한 것과 구분.'],
        ['검토 한계','고정된 소수 문서의 단일 AI 검토. 최신 정책이나 실제 수급 자격을 확인한 결과가 아님.'],
        ['자료 위치','experiments/model_evaluation/local_20260915/RUN_NOTES.md 및 모델별 JSONL/metadata.'],
        ['실행 설정','context 8192, 최대 출력 1024, temperature 0, seed 42, top_p 1, top_k 0, repeat_penalty 1, batch 128. Qwen think=false.']]
    books=[{'filename':'팀_질문세트_비교결과.xlsx','title':'팀 질문 8개 비교','note':common,'sheets':[
        sheet('비교요약',['모델','질문 이해 / 5점','안내문 표현 / 5점','호출 오류 / 건','JSON 형식 불일치 / 호출'],teamsummary,[24,23,23,19,24],
              'AI 참고 점수. 질문 6은 2배. 안내문 표현은 질문 2 제외. 지원자격의 정답률이 아닙니다.',teamformula),
        sheet('문항별평가',['모델','질문 번호','가중치','이해 점수','표현 점수','질문 이해 검토','안내문 표현 검토'],teamscores,[22,10,10,12,12,68,68],
              '원시 출력과 서비스 처리 결과를 함께 검토. 빈 표현 점수는 평가 제외입니다.'),
        sheet('질문과범위',['번호','원본 질문','원본 참고 항목','팀 예상 판정 / 참고','현재 자료에서 확인할 범위'],source_rows,[9,75,60,23,75],
              '원본 Excel을 수정하지 않았습니다. 예상 판정은 현재 자료의 정답으로 사용하지 않았습니다.'),
        sheet('답변전체',['모델','번호','내용 구분','순서','원문'],teamanswers,[22,10,35,10,105],
              '긴 문장은 여러 행으로 나눴습니다. 같은 구분의 순서대로 붙이면 원문과 같습니다.'),
        sheet('평가기준',['항목','설명'],rubricrows,[20,115]),
        sheet('실행조건',['모델','양자화','GPU 모듈','GPU 사용 / 서버 로그','모델 식별값'],conditions,[24,15,18,27,90],common)]},
        {'filename':'추가_품질평가_비교결과.xlsx','title':'별도 품질 평가 45개','note':common,'sheets':[
        sheet('비교요약',['모델','핵심 전달 / 35','근거 없는 최종 주장 / 35','답변 형식 실패 / 35','원문 인용 실패 / 35','기존 기준 통과 / 29','응답 중간값 / 초'],indmetrics,[23,22,27,25,24,24,22],
              '앞 2개 항목은 AI 검토, 나머지는 실행 기록. 분모와 뜻은 지표설명을 참고하세요.'),
        sheet('고정안내문비교',['모델','요약 채택 / 9개','핵심 전달 / 8문항','근거 없는 최종 주장 / 8문항','미호출 확인 / 2문항'],n13metrics,[24,24,25,30,25],
              '1문항은 정책 2개의 요약을 요청해 요약 수는 9개. 공통 공격문 노출도 최종 핵심 전달 실패에 포함.'),
        sheet('문항별평가',['모델','문항 ID','질문','서비스 처리','핵심 전달','근거 없는 주장','시간 / 초','AI 검토 이유'],inddetail,[22,24,62,32,14,17,13,82]),
        sheet('답변전체',['모델','문항 ID','내용 구분','순서','원문'],indraw,[22,24,40,10,105],
              '생성·자체 검사·최종 답변을 모두 보존. 긴 원문은 순서대로 나눴습니다.'),
        sheet('고정근거',['정책 ID','순서','공개 정책 자료'],policy_rows,[25,10,120],
              '저장소의 공개 평가용 정책 8개. 최신 정책의 사실 검증 자료는 아닙니다.'),
        sheet('지표설명',['항목','설명'],definitions,[33,120]),
        sheet('실행조건',['모델','양자화','GPU 모듈','GPU 사용 / 서버 로그','모델 식별값'],conditions,[24,15,18,27,90],common)]}]
    OUT.mkdir(parents=True,exist_ok=True)
    save(OUT/'artifact_input.json',{'workbooks':books})
    save(OUT/'result_summary.json',{'models':modeldata,'extras':extras,'team_summary':teamsummary,'independent_summary':indmetrics,'n13_summary':n13metrics})
    print('Prepared',len(teamscores),'team reviews,',len(inddetail),'independent rows')

if __name__=='__main__': main()
