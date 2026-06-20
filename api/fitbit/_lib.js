// Shared helpers for the Fitbit (Google Health API) OAuth 2.0 serverless functions.
//
// As of 2026 Google closed new app registration on dev.fitbit.com and routes all
// new Fitbit/Fitbit-Air integrations through the GOOGLE HEALTH API. So this "fitbit"
// integration authenticates with Google (accounts.google.com) and reads data from
// health.googleapis.com — NOT the legacy api.fitbit.com.
//
// The OAuth client (id + secret) is created in Google Cloud Console. The secret
// lives only here (server-side, from env). Tokens are kept in httpOnly cookies —
// never exposed to the browser. No database required.
const crypto = require('crypto');

const AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth';
const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const API_BASE = 'https://www.googleapis.com/health/v4';

// Two restricted scopes cover sleep + the heart-rate metrics we read.
// access_type=offline + prompt=consent are what make Google return a refresh token.
const SCOPES = [
  'https://www.googleapis.com/auth/googlehealth.sleep.readonly',
  'https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly',
].join(' ');

function getOrigin(req) {
  const proto = String(req.headers['x-forwarded-proto'] || 'https').split(',')[0].trim();
  const host = req.headers['x-forwarded-host'] || req.headers.host;
  return proto + '://' + host;
}
function redirectUri(req) { return getOrigin(req) + '/api/fitbit/callback'; }
function isHttps(req) { return getOrigin(req).startsWith('https'); }

function parseCookies(req) {
  const out = {};
  String(req.headers.cookie || '').split(';').forEach(p => {
    const i = p.indexOf('=');
    if (i > 0) out[p.slice(0, i).trim()] = decodeURIComponent(p.slice(i + 1).trim());
  });
  return out;
}
function cookie(name, val, opts) {
  opts = opts || {};
  let s = name + '=' + encodeURIComponent(val) + '; Path=/; HttpOnly; SameSite=Lax';
  if (opts.secure !== false) s += '; Secure';
  if (opts.maxAge != null) s += '; Max-Age=' + opts.maxAge;
  return s;
}
function clearCookie(name, secure) {
  return name + '=; Path=/; HttpOnly; SameSite=Lax' + (secure !== false ? '; Secure' : '') + '; Max-Age=0';
}

function creds() {
  // .trim() guards against stray spaces / newlines pasted into the Vercel env var.
  // We keep the FITBIT_* names so the rest of the suite + docs stay consistent,
  // even though these are now Google Cloud OAuth client credentials.
  const id = (process.env.FITBIT_CLIENT_ID || '').trim();
  const secret = (process.env.FITBIT_CLIENT_SECRET || '').trim();
  if (!id || !secret) { const e = new Error('FITBIT_NOT_CONFIGURED'); e.code = 'FITBIT_NOT_CONFIGURED'; throw e; }
  return { id, secret };
}

// Google's token endpoint takes client_id + client_secret in the form body.
async function tokenRequest(params) {
  const r = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(params).toString(),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = j.error_description || j.error || '';
    const e = new Error('token ' + r.status + ' ' + msg); e.status = r.status; throw e;
  }
  return j;
}

module.exports = { crypto, AUTH_URL, TOKEN_URL, API_BASE, SCOPES, getOrigin, redirectUri, isHttps, parseCookies, cookie, clearCookie, creds, tokenRequest };
