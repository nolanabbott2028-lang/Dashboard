// GET /api/fitbit/login — kicks off the Google Health OAuth flow.
const L = require('./_lib');

const CLIENT_ID = process.env.FITBIT_CLIENT_ID;
const REDIRECT_URI = 'https://dashboard-one-mauve-25.vercel.app/api/fitbit/callback';
const FIXED_STATE = 'dashboard-fitbit-2026';

module.exports = (req, res) => {
  if (!CLIENT_ID) {
    res.statusCode = 500;
    res.setHeader('content-type', 'text/html');
    res.end('<!doctype html><body style="font-family:system-ui;padding:2rem">FITBIT_CLIENT_ID env var not set in Vercel.</body>');
    return;
  }
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: CLIENT_ID,
    redirect_uri: REDIRECT_URI,
    scope: L.SCOPES,
    state: FIXED_STATE,
    access_type: 'offline',
    prompt: 'consent',
    include_granted_scopes: 'true',
  });
  res.statusCode = 302;
  res.setHeader('Location', L.AUTH_URL + '?' + params.toString());
  res.end();
};
