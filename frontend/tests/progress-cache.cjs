// Run against Vite with Playwright available: node tests/progress-cache.cjs
// API fixtures keep this regression independent of member data and paid LLM calls.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const profile = { id: 1, email: 'test@example.com', display_name: '테스트',
    created_at: '2026-09-20', region: '', gender: '', birth_date: '',
    disability_status: '', veteran_status: '', income_bracket: '',
    household_types: [], interests: [], membership_grade: '일반 회원' };
  const answer = (id, text) => ({ status: 'answered', session_id: id,
    question: null, missing_slots: [], interrupt_id: null, calc_missing_slots: [],
    calc_missing_choices: [], calc_slot_inputs: [], slot_conflicts: null,
    answer_status: 'complete', final_answer: text, final_citations: [], policies: [],
    output_json: {}, output_text: '', output_markdown: '', llm_status: {}, timing: {} });
  let recommendations = 0;
  let deletes = 0;
  let releaseRecommendation;
  let releaseChat;
  const polls = new Map();
  const requestTokens = [];
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const send = body => route.fulfill({ json: body });
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204 });
    if (path.endsWith('/users/me')) return send(profile);
    if (path.endsWith('/chat-defaults')) return send({});
    if (path.endsWith('/config/status')) return send({ status: 'ready' });
    if (path.includes('/config/')) return send({});
    if (path.endsWith('/chat/recommendations')) {
      recommendations++;
      const token = request.headers()['x-progress-token'];
      assert.ok(token);
      requestTokens.push(token);
      await new Promise(resolve => { releaseRecommendation = resolve; });
      const result = answer('home-session', '캐시된 추천 결과입니다.');
      result.policies = [{ policy_id: 'fixture-policy', title: '캐시된 추천 결과입니다.',
        rank: 1, eligibility_status: '충족', badge: '자격 충족', amount_label: '월 10만원',
        duplicate_status: '미확인', detail: { region_scope: 'national' } }];
      return send(result);
    }
    if (path.endsWith('/chat/messages') || path.endsWith('/followup')) {
      const token = request.headers()['x-progress-token'];
      assert.ok(token);
      requestTokens.push(token);
      await new Promise(resolve => { releaseChat = resolve; });
      return send(answer('chat-session', '상담 결과입니다.'));
    }
    if (path.includes('/chat/progress/')) {
      const token = path.split('/').pop();
      assert.ok(requestTokens.includes(token), 'poll the token sent with the POST');
      const count = (polls.get(token) || 0) + 1;
      polls.set(token, count);
      return send({ status: 'running', fraction: count < 2 ? 0.2 : 0.6,
        message: count < 2 ? '지원 제도를 찾고 있어요' : '근거를 확인하고 있어요',
        completed_steps: count < 2 ? 2 : 6, total_steps: 10, elapsed_seconds: count });
    }
    if (request.method() === 'DELETE') { deletes++; return send({ message: '삭제' }); }
    throw new Error(`Unexpected request: ${request.method()} ${path}`);
  });
  try {
    await page.goto(`${process.env.TEST_BASE_URL || 'http://127.0.0.1:5178'}/home`);
    const bar = page.getByRole('progressbar');
    await bar.waitFor();
    await page.waitForFunction(() => document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow') === '60');
    // Leave while the recommendation POST is still pending; resume its token on return.
    await page.getByRole('link', { name: '마이페이지' }).click();
    await page.getByRole('button', { name: '홈으로 이동' }).click();
    await page.waitForFunction(() => document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow') === '60');
    assert.equal(recommendations, 1);
    releaseRecommendation();
    await page.getByRole('checkbox', { name: '캐시된 추천 결과입니다. 비교 선택' }).waitFor();
    await page.getByRole('button', { name: '새 상담 시작', exact: true }).click();
    await page.getByRole('button', { name: '새 상담 시작', exact: true }).last().click();
    await page.waitForURL('**/chat');
    assert.equal(deletes, 0, 'starting chat must preserve the home session');
    await page.getByRole('textbox').fill('지원 제도를 알려주세요');
    await page.getByRole('button', { name: '전송', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow') === '60');
    releaseChat();
    await page.getByText('상담 결과입니다.', { exact: true }).waitFor();
    assert.notEqual(requestTokens[0], requestTokens[1]);
    await page.getByRole('button', { name: '홈으로 이동' }).click();
    await page.getByRole('checkbox', { name: '캐시된 추천 결과입니다. 비교 선택' }).waitFor();
    assert.equal(recommendations, 1, 'home must render cached results without another POST');
    assert.equal(await bar.count(), 0);
    await page.goto(`${process.env.TEST_BASE_URL || 'http://127.0.0.1:5178'}/signup`);
    assert.equal(await page.getByRole('checkbox').count(), 2);
    assert.equal(await page.getByText('혜택·안내 정보 수신에 동의합니다.').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: signup, home/chat API progress, pending navigation, cached home revisit');
  } finally {
    releaseRecommendation?.();
    releaseChat?.();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
