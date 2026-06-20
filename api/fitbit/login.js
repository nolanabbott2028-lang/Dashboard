// GET /api/fitbit/login — kicks off the Google Health OAuth flow
// (302 → accounts.google.com consent). access_type=offline + prompt=consent
// ensure Google returns a refresh token.
const L = require('./_lib');

module.exports = (req, res) => {
  let id;
  try { id = L.creds().id; }
  catch (e) {
    res.statusCode = 500;
    res.setHeader('content-type', 'text/html');
    res.end('<!doctype html><meta charset="utf-8"><body style="font-family:system-ui;max-width:34rem;margin:4rem auto;line-height:1.5;color:#222">'
      + '<h2>Fitbit (Google Health) isn’t configured yet</h2><p>Set <code>FITBIT_CLIENT_ID</code> and <code>FITBIT_CLIENT_SECRET</code> in your Vercel project’s Environment Variables (these are your <em>Google Cloud</em> OAuth client credentials), and register <code>' + L.redirectUri(req) + '</code> as an Authorized redirect URI in the Google Cloud Console. See <code>FITBIT_SETUP.md</code>.</p><p><a href="/">← back to the dashboard</a></p></body>');
    return;
  }
  const state = L.crypto.randomBytes(12).toString('hex');
  res.setHeader('Set-Cookie', L.cookie('fitbit_state', state, { maxAge: 600, secure: L.isHttps(req) }));
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: id,
    redirect_uri: L.redirectUri(req),
    scope: L.SCOPES,
    state,
    access_type: 'offline',
    prompt: 'consent',
    include_granted_scopes: 'true',
  });
  res.statusCode = 302;
  res.setHeader('Location', L.AUTH_URL + '?' + params.toString());
  res.end();
};
