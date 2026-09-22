const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1100, height: 950 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const policy = (id, title) => ({
    rank: 1, policy_id: id, title, badge: '일부 조건 확인', eligibility_status: '충족',
    eligibility_reasons: [], verification_checked: ['연령'], verification_unchecked: ['소득'],
    verification_note: '연령 조건을 확인했습니다. 나머지 조건은 원문을 확인해주세요.',
    amount_label: '1,000,000원', duplicate_status: '미확인', duplicate_conflicts: [],
    household_limit_clauses: [], needs_confirmation: [], related_law: [],
    detail: { region_scope: 'national', purpose: '검증용 정책 설명', support_details: '지원내용 원문' },
  });
  let count = 0;
  let release;
  const questions = [];
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const send = json => route.fulfill({ json });
    if (path.endsWith('/users/me')) return send({ id: 1, display_name: '테스트', interests: [] });
    if (path.endsWith('/chat-defaults') || path.includes('/config/')) return send({});
    if (path.includes('/progress/')) return send({ status: 'running', fraction: 0.5, completed_steps: 5, total_steps: 10 });
    if (path.endsWith('/questions')) {
      questions.push(req.postDataJSON().question);
      return send({ kind: 'answer', text: '정책 상세 답변입니다.', evidence_quotes: [], reason: null, llm_status: {} });
    }
    if (path.endsWith('/messages') || path.endsWith('/followup')) {
      count++;
      if (count === 2) await new Promise(resolve => { release = resolve; });
      return send({ status: 'answered', session_id: 'session', missing_slots: [],
        calc_missing_slots: [], calc_missing_choices: [], calc_slot_inputs: [],
        answer_status: 'complete', final_answer: `텍스트 대체 금지 ${count}`, final_citations: [],
        policies: count === 1 ? [policy('a', '이전 정책'), policy('b', '이전 정책 2')] : [policy('c', '첫만남 이용권')],
        output_json: {}, llm_status: {}, timing: {} });
    }
    throw new Error(`Unexpected API: ${path}`);
  });
  try {
    await page.goto(`${process.env.TEST_BASE_URL || 'http://127.0.0.1:5178'}/chat`);
    const input = page.getByRole('textbox');
    await input.fill('첫 상담');
    await page.getByRole('button', { name: '전송', exact: true }).click();
    const first = page.getByRole('region', { name: '상담 정책 결과' }).first();
    const selected = first.getByRole('checkbox', { name: '이전 정책 비교 선택', exact: true });
    await selected.check();
    const boxes = await first.locator('.policy-card-tags').first().locator(':scope > span').evaluateAll(nodes =>
      nodes.map(node => { const r = node.getBoundingClientRect(); return { top: r.top, height: r.height }; }));
    assert.equal(boxes.length, 3);
    assert.ok(boxes.every(box => Math.abs(box.top - boxes[0].top) < 1 && Math.abs(box.height - boxes[0].height) < 1));
    await input.fill('추가 상담');
    await page.getByRole('button', { name: '전송', exact: true }).click();
    await page.getByRole('progressbar').waitFor();
    assert.ok(await selected.isChecked(), 'keep the previous result while sending');
    await page.waitForFunction(() => document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow') === '50');
    release();
    await page.getByRole('checkbox', { name: '첫만남 이용권 비교 선택' }).waitFor();
    assert.equal(await page.getByRole('region', { name: '상담 정책 결과' }).count(), 2);
    assert.ok(await selected.isChecked(), 'keep per-turn selections after the next answer');
    assert.equal(await page.getByText(/텍스트 대체 금지/).count(), 0);
    await first.getByRole('button', { name: '자세히 보기' }).first().click();
    await first.getByText('지원자격', { exact: true }).waitFor();
    await first.getByRole('button', { name: '목록으로' }).click();
    assert.ok(await selected.isChecked());
    const latest = page.getByRole('region', { name: '상담 정책 결과' }).last();
    await latest.getByRole('button', { name: '자세히 보기' }).click();
    await latest.getByRole('button', { name: '이 정책에 대해 추가 질문하기' }).click();
    const questionInput = page.getByPlaceholder('첫만남 이용권에 대해 물어보세요');
    for (const [i, text] of ['자녀수별로 다른 지원금 알려줘', '정책 상세 알려줘'].entries()) {
      await questionInput.fill(text);
      await questionInput.press('Enter');
      await page.getByText('정책 상세 답변입니다.', { exact: true }).nth(i).waitFor();
      await questionInput.waitFor({ state: 'visible' });
      await page.waitForFunction(() => !document.querySelector('input[placeholder="첫만남 이용권에 대해 물어보세요"]').disabled);
    }
    assert.deepEqual(questions, ['자녀수별로 다른 지원금 알려줘', '정책 상세 알려줘']);
    assert.deepEqual(errors, []);
    console.log('PASS: multi-turn cards, selection/detail preservation, chip alignment, declarative policy requests');
  } finally { release?.(); await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
