// GET /api/fitbit/callback — Google redirects here with ?code & ?state.
const L = require('./_lib');

const CLIENT_ID = process.env.FITBIT_CLIENT_ID;
const CLIENT_SECRET = process.env.FITBIT_CLIENT_SECRET;
const FIXED_STATE = 'dashboard-fitbit-2026';
const REDIRECT_URI = 'https://dashboard-one-mauve-25.vercel.app/api/fitbit/callback';

function showError(res, title, detail) {
  res.statusCode = 200;
  res.setHeader('content-type', 'text/html');
  res.end(`<!doctype html><meta charset="utf-8">
<body style="font-family:system-ui;max-width:36rem;margin:3rem auto;padding:1rem;background:#0a0a0a;color:#eee">
<h2 style="color:#f87171">Fitbit connection error</h2>
<p><strong>${title}</strong></p>
<pre style="background:#1a1a1a;padding:1rem;border-radius:8px;white-space:pre-wrap;font-size:13px">${detail}</pre>
<p><a href="/fitband.html" style="color:#60a5fa">← back to dashboard</a></p>
</body>`);
}

module.exports = async (req, res) => {
  const origin = L.getOrigin(req);
  const url = new URL(req.url, origin);
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  const oauthErr = url.searchParams.get('error');
  const secure = L.isHttps(req);

  if (!CLIENT_ID || !CLIENT_SECRET) return showError(res, 'Not configured', 'FITBIT_CLIENT_ID / FITBIT_CLIENT_SECRET env vars missing in Vercel.');
  if (oauthErr) return showError(res, 'Google denied the request', 'error=' + oauthErr);
  if (!code) return showError(res, 'No authorization code received', JSON.stringify(Object.fromEntries(url.searchParams), null, 2));
  if (state !== FIXED_STATE) return showError(res, 'State mismatch', 'received: ' + state);

  try {
    const tok = await L.tokenRequest({
      grant_type: 'authorization_code',
      code,
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      redirect_uri: REDIRECT_URI,
    });

    if (!tok.refresh_token) return showError(res, 'No refresh token returned', JSON.stringify(tok, null, 2));

    res.setHeader('Set-Cookie', L.cookie('fitbit_refresh', tok.refresh_token, { maxAge: 60 * 60 * 24 * 180, secure }));
    res.statusCode = 302;
    res.setHeader('Location', '/fitband.html?fitbit=connected');
    res.end();
  } catch (e) {
    return showError(res, 'Token exchange failed: ' + e.message, e.detail || '');
  }
};
