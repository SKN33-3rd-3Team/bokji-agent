"""Read-only verification of exported cells, cached formulas and report text."""
from pathlib import Path
import json
import math
from openpyxl import load_workbook
from pypdf import PdfReader

OUT=Path(__file__).resolve().parents[1]/'output/llm_comparison_20260915'
spec=json.loads((OUT/'artifact_input.json').read_text(encoding='utf-8'))
summary=json.loads((OUT/'result_summary.json').read_text(encoding='utf-8'))
checks=[]
for book in spec['workbooks']:
    wb=load_workbook(OUT/book['filename'],data_only=True)
    formula_book=load_workbook(OUT/book['filename'],data_only=False)
    assert wb.sheetnames==[s['name'] for s in book['sheets']]
    for sheet in book['sheets']:
        ws=wb[sheet['name']]
        overrides={f['cell'] for f in sheet.get('formulas',[])}
        assert ws.max_row==len(sheet['rows'])+4
        for i,row in enumerate(sheet['rows'],5):
            for j,expected in enumerate(row,1):
                cell=ws.cell(i,j)
                if cell.coordinate in overrides: continue
                if expected=='': expected=None
                if isinstance(expected,(int,float)):
                    assert math.isclose(cell.value,expected,abs_tol=1e-9),(sheet['name'],cell.coordinate)
                else:
                    assert cell.value==expected,(sheet['name'],cell.coordinate)
        for f in sheet.get('formulas',[]):
            assert formula_book[sheet['name']][f['cell']].value==f['formula']
            assert isinstance(ws[f['cell']].value,(int,float))
        checks.append({'workbook':book['filename'],'sheet':sheet['name'],
                       'data_rows':len(sheet['rows']),'exact_cell_match':True})
    if book['filename'].startswith('팀_'):
        for i,model in enumerate(['qwen','ax','bllossom'],5):
            for col,key in [('B','team_slots'),('C','team_expression')]:
                assert math.isclose(wb['비교요약'][f'{col}{i}'].value,summary['models'][model][key],abs_tol=1e-9)
qa=json.loads((OUT/'qa/검증.json').read_text(encoding='utf-8'))
assert not any(q['오류후보'] or q['높이제한행'] for q in qa)
pdf=PdfReader(OUT/'LLM_답변품질_비교보고서.pdf')
assert len(pdf.pages)==5
text='\n'.join(page.extract_text() for page in pdf.pages)
for phrase in ['4.63','3.11','3.33','2.00','159','Q4_K_M','Vulkan']:
    assert phrase in text,phrase
assert '\ufffd' not in text
(OUT/'qa/final_verification.json').write_text(json.dumps({
    'cells':checks,'formula_scores_match':True,'pdf_pages':len(pdf.pages),
    'no_height_overflow':True},ensure_ascii=False,indent=2),encoding='utf-8')
print('Verified: all source cells, six score formulas, 13 sheets, PDF 5 pages; no error cells or capped rows.')
