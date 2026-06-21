// GET /api/fitbit/callback — Google redirects here with ?code & ?state.
// Exchanges the code for tokens and stores the refresh token in a cookie.
const L = require('./_lib');

const CLIENT_ID = '1055570996751-tjluflqiu01k83dush5pecg9gg7mil60.apps.googleusercontent.com';
const CLIENT_SECRET = 'G0CSPX-bSHSlRKOghXCusq3lFsg2300r1dB';
const FIXED_STATE = 'dashboard-fitbit-2026';
const REDIRECT_URI = 'https://dashboard-one-mauve-25.vercel.app/api/fitbit/callback';

module.exports = async (req, res) => {
  const origin = L.getOrigin(req);
  const url = new URL(req.url, origin);
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  const oauthErr = url.searchParams.get('error');
  const secure = L.isHttps(req);
  const back = (status, detail) => {
    const loc = '/fitband.html?fitbit=' + status + (detail ? '&detail=' + encodeURIComponent(detail) : '');
    res.statusCode = 302; res.setHeader('Location', loc); res.end();
  };

  if (oauthErr) {
    console.error('[fitbit/callback] oauth error:', oauthErr);
    return back('denied');
  }
  if (!code) {
    console.error('[fitbit/callback] no code in request');
    return back('error', 'no_code');
  }
  if (state !== FIXED_STATE) {
    console.error('[fitbit/callback] unexpected state:', state);
    return back('error', 'bad_state');
  }

  try {
    const tok = await L.tokenRequest({
      grant_type: 'authorization_code',
      code,
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      redirect_uri: REDIRECT_URI,
    });
    console.log('[fitbit/callback] token ok. has_refresh:', !!tok.refresh_token, 'has_access:', !!tok.access_token);
    if (tok.refresh_token) {
      res.setHeader('Set-Cookie', L.cookie('fitbit_refresh', tok.refresh_token, { maxAge: 60 * 60 * 24 * 180, secure }));
    }
    return back(tok.refresh_token ? 'connected' : 'error', tok.refresh_token ? null : 'no_refresh_token');
  } catch (e) {
    console.error('[fitbit/callback] token exchange failed:', e.message);
    return back('error', e.message);
  }
};
