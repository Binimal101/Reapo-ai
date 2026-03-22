const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1512, height: 982 } });

  const projects = [
    { project_id: 'p1', owner_user_id: 'u1', name: 'Alpha', description: 'desc', role: 'owner' },
    { project_id: 'p2', owner_user_id: 'u1', name: 'Beta', description: 'desc', role: 'owner' },
    { project_id: 'p3', owner_user_id: 'u1', name: 'Gamma', description: 'desc', role: 'owner' }
  ];

  const repos = Array.from({ length: 16 }).map((_, i) => ({
    id: i + 1,
    owner: 'acme',
    name: `repo-${i + 1}`,
    full_name: `acme/repo-${i + 1}`,
    private: false,
    visibility: 'public',
    default_branch: 'main'
  }));

  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    const m = req.method();
    const ok = (body, status = 200) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });

    if (p === '/api/auth/session/validate' && m === 'POST') return ok({ status: 'ok', user_id: 'u1' });
    if (p === '/api/auth/github/user-repos' && m === 'GET') return ok({ status: 'ok', repositories: repos });
    if (p === '/api/projects' && m === 'GET') return ok({ status: 'ok', projects });
    if (p === '/api/projects' && m === 'POST') return ok({ status: 'ok', project: projects[0] }, 201);
    if (/^\/api\/projects\/[^/]+\/repositories$/.test(p) && m === 'GET') return ok({ status: 'ok', repositories: [] });
    if (/^\/api\/projects\/[^/]+$/.test(p) && m === 'PATCH') return ok({ status: 'ok', project: projects[0] });
    if (/^\/api\/projects\/[^/]+$/.test(p) && m === 'DELETE') return ok({ status: 'ok', deleted: true });
    if (/^\/api\/projects\/[^/]+\/repositories$/.test(p) && m === 'POST') return ok({ status: 'ok', repositories: [] });
    if (/^\/api\/projects\/[^/]+\/repositories\/\d+$/.test(p) && m === 'DELETE') return ok({ status: 'ok', removed: true, repositories: [] });

    return ok({ status: 'error', reason: 'not_found', p, m }, 404);
  });

  await page.addInitScript(() => localStorage.setItem('reapo_session_token', 'debug-token'));
  await page.goto('http://localhost:5177/projects', { waitUntil: 'networkidle' });
  await page.screenshot({ path: 'tmp/projects-layout-current.png', fullPage: true });
  await browser.close();
})();
