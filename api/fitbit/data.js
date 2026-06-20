// GET /api/fitbit/data — refreshes the access token, then reads recent sleep,
// resting heart rate, and HRV from the GOOGLE HEALTH API (health.googleapis.com)
// and returns a vitals payload matching what fitband.html's buildLiveData() reads:
//   recovery (null — no recovery score), hrv, rhr, sleepPerf, sleepHours,
//   strain (null), bedtime, wakeTime, plus sleepDebt7d history.
//
// NOTE: the Google Health API is new and the public docs don't pin every
// kebab-case dataType name, so each fetch is defensive — we try the most likely
// dataType path(s) and parse common field shapes, returning null for anything we
// can't find rather than failing the whole request.
const L = require('./_lib');

async function listPoints(dataType, token, params) {
  const qs = params ? ('?' + new URLSearchParams(params).toString()) : '';
  try {
    const r = await fetch(L.API_BASE + '/users/me/dataTypes/' + dataType + '/dataPoints' + qs, {
      headers: { Authorization: 'Bearer ' + token },
    });
    if (!r.ok) return [];
    const j = await r.json().catch(() => null);
    if (!j) return [];
    return Array.isArray(j.dataPoints) ? j.dataPoints : (Array.isArray(j.dataPoint) ? j.dataPoint : []);
  } catch (e) { return []; }
}
// Try several dataType spellings; return the first that yields points.
async function firstOf(names, token, params) {
  for (const n of names) {
    const pts = await listPoints(n, token, params);
    if (pts.length) return pts;
  }
  return [];
}

// Pull a numeric value out of a data point regardless of nesting depth.
function num(obj, keys) {
  if (obj == null) return null;
  if (typeof obj === 'number') return obj;
  if (typeof obj !== 'object') return null;
  for (const k of keys) if (typeof obj[k] === 'number') return obj[k];
  for (const v of Object.values(obj)) {
    const r = num(v, keys);
    if (r != null) return r;
  }
  return null;
}
// "2026-06-20T22:45:00..." → "22:45"
function clock(s) {
  if (!s || typeof s !== 'string') return null;
  const m = s.match(/T(\d{2}):(\d{2})/);
  return m ? m[1] + ':' + m[2] : null;
}
function startOf(p) { return p.startTime || p.startTimeNanos || (p.interval && p.interval.startTime) || p.effectiveTime || null; }
function endOf(p) { return p.endTime || p.endTimeNanos || (p.interval && p.interval.endTime) || null; }

module.exports = async (req, res) => {
  res.setHeader('content-type', 'application/json');
  const cookies = L.parseCookies(req);
  const secure = L.isHttps(req);
  const refresh = cookies.fitbit_refresh;
  if (!refresh) { res.statusCode = 200; res.end(JSON.stringify({ connected: false })); return; }

  let id, secret;
  try { ({ id, secret } = L.creds()); }
  catch (e) { res.statusCode = 200; res.end(JSON.stringify({ connected: false, error: 'not_configured' })); return; }

  let tok;
  try {
    tok = await L.tokenRequest({ grant_type: 'refresh_token', refresh_token: refresh, client_id: id, client_secret: secret });
  } catch (e) {
    res.statusCode = 200;
    res.setHeader('Set-Cookie', L.clearCookie('fitbit_refresh', secure));
    res.end(JSON.stringify({ connected: false, error: 'expired' }));
    return;
  }
  // Google may return a new refresh token; persist it if so.
  if (tok.refresh_token && tok.refresh_token !== refresh) {
    res.setHeader('Set-Cookie', L.cookie('fitbit_refresh', tok.refresh_token, { maxAge: 60 * 60 * 24 * 180, secure }));
  }
  const at = tok.access_token;

  const now = new Date();
  const weekAgo = new Date(now.getTime() - 6 * 86400000);
  const window = { startTime: weekAgo.toISOString(), endTime: now.toISOString(), pageSize: '30' };

  const [sleepPts, rhrPts, hrvPts] = await Promise.all([
    firstOf(['sleep'], at, window),
    firstOf(['daily-resting-heart-rate', 'resting-heart-rate'], at, window),
    firstOf(['daily-heart-rate-variability', 'heart-rate-variability'], at, window),
  ]);

  // newest first by start time
  const byNewest = (a, b) => new Date(endOf(b) || startOf(b) || 0) - new Date(endOf(a) || startOf(a) || 0);
  sleepPts.sort(byNewest);

  let sleepHours = null, sleepPerf = null, bedtime = null, wakeTime = null;
  const mainSleep = sleepPts.find(p => p.isMainSleep || (p.sleep && p.sleep.isMainSleep)) || sleepPts[0] || null;
  if (mainSleep) {
    const mins = num(mainSleep, ['minutesAsleep', 'asleepDurationMinutes', 'sleepDurationMinutes', 'durationMinutes']);
    if (mins != null) sleepHours = Math.round((mins / 60) * 100) / 100;
    if (sleepHours == null) {
      const start = startOf(mainSleep), end = endOf(mainSleep);
      if (start && end) sleepHours = Math.round(((new Date(end) - new Date(start)) / 3600000) * 100) / 100;
    }
    const eff = num(mainSleep, ['efficiency', 'sleepEfficiency', 'efficiencyPercentage']);
    if (eff != null) sleepPerf = Math.round(eff);
    bedtime = clock(startOf(mainSleep));
    wakeTime = clock(endOf(mainSleep));
  }

  rhrPts.sort(byNewest);
  let rhr = num(rhrPts[0], ['restingHeartRate', 'beatsPerMinute', 'bpm', 'value']);
  if (rhr != null) rhr = Math.round(rhr);

  hrvPts.sort(byNewest);
  let hrv = num(hrvPts[0], ['dailyRmssd', 'rmssd', 'heartRateVariabilityMillis', 'value']);
  if (hrv != null) hrv = Math.round(hrv);

  const sleepDebt7d = sleepPts
    .map(p => {
      const s = startOf(p);
      const day = s ? new Date(s).toLocaleDateString('en-US', { weekday: 'short' }) : '';
      const mins = num(p, ['minutesAsleep', 'asleepDurationMinutes', 'sleepDurationMinutes', 'durationMinutes']);
      const hours = mins != null ? Math.round((mins / 60) * 100) / 100 : null;
      return hours != null ? { day, hours } : null;
    })
    .filter(Boolean)
    .reverse();

  res.statusCode = 200;
  res.end(JSON.stringify({
    connected: true, source: 'fitbit', ts: Date.now(),
    recovery: null,          // Google Health / Fitbit has no WHOOP-style recovery score
    hrv,
    rhr,
    sleepPerf,
    sleepHours,
    strain: null,            // no strain metric
    bedtime,
    wakeTime,
    sleepTargetHours: 8,
    recoveryTrend: [],
    sleepDebt7d,
    strainWeeklyAvg: null,
  }));
};
