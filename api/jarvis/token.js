// GET /api/jarvis/token — issues a LiveKit token for the browser to join the JARVIS room.
// Uses the AccessToken from livekit-server-sdk (installed as a Vercel dependency).
const { AccessToken } = require('livekit-server-sdk');

const LK_API_KEY = process.env.LIVEKIT_API_KEY;
const LK_API_SECRET = process.env.LIVEKIT_API_SECRET;
const ROOM = 'jarvis-room';

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('content-type', 'application/json');

  if (!LK_API_KEY || !LK_API_SECRET) {
    res.statusCode = 500;
    res.end(JSON.stringify({ error: 'LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set' }));
    return;
  }

  const identity = 'user-' + Date.now();
  const at = new AccessToken(LK_API_KEY, LK_API_SECRET, { identity, ttl: '4h' });
  at.addGrant({ roomJoin: true, room: ROOM, canPublish: true, canSubscribe: true });
  const token = await at.toJwt();

  res.statusCode = 200;
  res.end(JSON.stringify({
    token,
    url: process.env.LIVEKIT_URL,
    room: ROOM,
  }));
};
