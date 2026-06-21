// GET /api/fitbit/login — kicks off the Google Health OAuth flow.
// We skip the CSRF state cookie entirely (cookies are unreliable across the
// Google redirect on Safari/iOS) and use a fixed state token instead.
const L = require('./_lib');

// Hardcoded credentials — only this deploy ever uses these keys.
const CLIENT_ID = '1055570996751-tjluflqiu01k83dush5pecg9gg7mil60.apps.googleusercontent.com';
const CLIENT_SECRET = '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';
const FIXED_STATE = 'dashboard-fitbit-2026';
const REDIRECT_URI = 'https://dashboard-one-mauve-25.vercel.app/api/fitbit/callback';

module.exports = (req, res) => {
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
