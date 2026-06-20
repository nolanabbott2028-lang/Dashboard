// GET /api/fitbit/data — refreshes the access token (rotating the stored refresh
// token), fetches recent sleep / resting HR / HRV, and returns a vitals payload
// matching what fitband.html's buildLiveData() reads:
//   recovery (null — Fitbit has none), hrv, rhr, sleepPerf, sleepHours, strain (null),
//   bedtime, wakeTime, plus sleepDebt7d history.
// Same-origin, so the browser hits it with no CORS.
const L = require('./_lib');

async function fGet(path, token) {
  try {
    const r = await fetch(L.API_BASE + path, { headers: { Authorization: 'Bearer ' + token } });
    if (!r.ok) return null;
    return await r.json().catch(() => null);
  } catch (e) { return null; }
}

function ymd(d) { return d.toISOString().slice(0, 10); }

// Fitbit sleep times come as "yyyy-MM-ddTHH:mm:ss.SSS" (local, no zone) — take HH:mm.
function clock(s) {
  if (!s || typeof s !== 'string') return null;
  const m = s.match(/T(\d{2}):(\d{2})/);
  return m ? m[1] + ':' + m[2] : null;
}

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
    tok = await L.tokenRequest({ grant_type: 'refresh_token', refresh_token: refresh }, id, secret);
  } catch (e) {
    res.statusCode = 200;
    res.setHeader('Set-Cookie', L.clearCookie('fitbit_refresh', secure));
    res.end(JSON.stringify({ connected: false, error: 'expired' }));
    return;
  }
  // Fitbit rotates refresh tokens — persist the new one.
  if (tok.refresh_token && tok.refresh_token !== refresh) {
    res.setHeader('Set-Cookie', L.cookie('fitbit_refresh', tok.refresh_token, { maxAge: 60 * 60 * 24 * 365, secure }));
  }
  const at = tok.access_token;

  const today = new Date();
  const weekAgo = new Date(today.getTime() - 6 * 86400000);

  // Sleep log range (last 7 days), resting HR (today), and HRV (today).
  const [sleepRange, hrToday, hrvToday] = await Promise.all([
    fGet('/1.2/user/-/sleep/date/' + ymd(weekAgo) + '/' + ymd(today) + '.json', at),
    fGet('/1/user/-/activities/heart/date/today/1d.json', at),
    fGet('/1/user/-/hrv/date/today.json', at),
  ]);

  const sleeps = (sleepRange && Array.isArray(sleepRange.sleep)) ? sleepRange.sleep : [];
  // Newest first.
  sleeps.sort((a, b) => new Date(b.startTime) - new Date(a.startTime));
  const main = sleeps.find(s => s.isMainSleep) || sleeps[0] || null;

  let sleepHours = null, sleepPerf = null, bedtime = null, wakeTime = null;
  if (main) {
    if (main.minutesAsleep != null) sleepHours = Math.round((main.minutesAsleep / 60) * 100) / 100;
    if (main.efficiency != null) sleepPerf = Math.round(main.efficiency);
    bedtime = clock(main.startTime);
    wakeTime = clock(main.endTime);
  }

  // Resting heart rate (Fitbit "restingHeartRate" on the day's activities-heart record).
  let rhr = null;
  if (hrToday && Array.isArray(hrToday['activities-heart']) && hrToday['activities-heart'][0]) {
    const v = hrToday['activities-heart'][0].value;
    if (v && v.restingHeartRate != null) rhr = Math.round(v.restingHeartRate);
  }

  // HRV (daily RMSSD).
  let hrv = null;
  if (hrvToday && Array.isArray(hrvToday.hrv) && hrvToday.hrv[0] && hrvToday.hrv[0].value) {
    const v = hrvToday.hrv[0].value.dailyRmssd;
    if (v != null) hrv = Math.round(v);
  }

  // 7-day sleep-debt history (oldest → newest) for the standalone trend view.
  const sleepDebt7d = sleeps
    .map(s => {
      const day = s.startTime ? new Date(s.startTime).toLocaleDateString('en-US', { weekday: 'short' }) : '';
      const hours = s.minutesAsleep != null ? Math.round((s.minutesAsleep / 60) * 100) / 100 : null;
      return hours != null ? { day, hours } : null;
    })
    .filter(Boolean)
    .reverse();

  res.statusCode = 200;
  res.end(JSON.stringify({
    connected: true, source: 'fitbit', ts: Date.now(),
    recovery: null,          // Fitbit has no WHOOP-style recovery score
    hrv,
    rhr,
    sleepPerf,
    sleepHours,
    strain: null,            // Fitbit has no strain metric
    bedtime,
    wakeTime,
    sleepTargetHours: 8,
    recoveryTrend: [],
    sleepDebt7d,
    strainWeeklyAvg: null,
  }));
};
