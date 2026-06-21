// GET /api/jarvis/token — issues a LiveKit JWT for the browser to join the JARVIS room.
// CJS module — do NOT add "type":"module" to package.json (livekit-server-sdk v2 CJS build)
//
// Fixes applied (audit 2026-06-21):
//   Issue 11: ttl as numeric seconds (not '4h' string — unreliable across v2.x minor versions)
//   Issue 12: CORS restricted to known dashboard origin, not wildcard *
//   Issue 13: LIVEKIT_URL validated before use
//   Issue 14: OPTIONS preflight handled correctly
const { AccessToken } = require('livekit-server-sdk');

const LK_API_KEY    = process.env.LIVEKIT_API_KEY;
const LK_API_SECRET = process.env.LIVEKIT_API_SECRET;
const LK_URL        = process.env.LIVEKIT_URL;
const ROOM          = 'jarvis-room';

// Issue 12: restrict CORS to the known dashboard origin
const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN || 'https://dashboard-one-mauve-25.vercel.app';

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', ALLOWED_ORIGIN);
  res.setHeader('Vary', 'Origin');
  res.setHeader('content-type', 'application/json');

  // Issue 14: handle CORS preflight
  if (req.method === 'OPTIONS') {
    res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    res.statusCode = 204;
    res.end();
    return;
  }

  // Validate all required env vars
  if (!LK_API_KEY || !LK_API_SECRET) {
    res.statusCode = 500;
    res.end(JSON.stringify({ error: 'LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set' }));
    return;
  }

  // Issue 13: validate LIVEKIT_URL before returning it
  if (!LK_URL) {
    res.statusCode = 500;
    res.end(JSON.stringify({ error: 'LIVEKIT_URL not set' }));
    return;
  }

  const identity = 'user-' + Date.now();
  const at = new AccessToken(LK_API_KEY, LK_API_SECRET, {
    identity,
    ttl: 14400, // Issue 11: 4 hours in seconds — numeric, safe across all v2.x
  });
  at.addGrant({ roomJoin: true, room: ROOM, canPublish: true, canSubscribe: true });
  const token = await at.toJwt();

  res.statusCode = 200;
  res.end(JSON.stringify({ token, url: LK_URL, room: ROOM }));
};
