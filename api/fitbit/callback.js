// GET /api/fitbit/callback — Google redirects here with ?code & ?state.
// Verifies state, exchanges the code for tokens (server-side, with the secret),
// stores the refresh token in an httpOnly cookie, and returns to the dashboard.
const L = require('./_lib');

module.exports = async (req, res) => {
  const origin = L.getOrigin(req);
  const url = new URL(req.url, origin);
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  const oauthErr = url.searchParams.get('error');
  const cookies = L.parseCookies(req);
  const secure = L.isHttps(req);
  const back = (status, detail) => {
    const loc = '/fitband.html?fitbit=' + status + (detail ? '&detail=' + encodeURIComponent(detail) : '');
    res.statusCode = 302; res.setHeader('Location', loc); res.end();
  };

  if (oauthErr) {
    console.error('[fitbit/callback] oauth error from Google:', oauthErr);
    return back('denied');
  }
  if (!code || !state) {
    console.error('[fitbit/callback] missing code or state. code:', !!code, 'state:', !!state);
    return back('error', 'missing_code_or_state');
  }
  if (state !== cookies.fitbit_state) {
    console.error('[fitbit/callback] state mismatch. got:', state, 'cookie keys:', Object.keys(cookies).join(','));
    return back('error', 'state_mismatch');
  }

  let id, secret;
  try { ({ id, secret } = L.creds()); }
  catch (e) {
    console.error('[fitbit/callback] creds missing:', e.message);
    res.statusCode = 500; res.end('Fitbit (Google Health) not configured'); return;
  }

  try {
    const tok = await L.tokenRequest({
      grant_type: 'authorization_code',
      code,
      client_id: id,
      client_secret: secret,
      redirect_uri: L.redirectUri(req),
    });
    console.log('[fitbit/callback] token ok. has_refresh:', !!tok.refresh_token, 'has_access:', !!tok.access_token);
    const out = [L.clearCookie('fitbit_state', secure)];
    if (tok.refresh_token) out.push(L.cookie('fitbit_refresh', tok.refresh_token, { maxAge: 60 * 60 * 24 * 180, secure }));
    res.setHeader('Set-Cookie', out);
    return back(tok.refresh_token ? 'connected' : 'error', tok.refresh_token ? null : 'no_refresh_token');
  } catch (e) {
    console.error('[fitbit/callback] token exchange failed:', e.message);
    return back('error', e.message);
  }
};
