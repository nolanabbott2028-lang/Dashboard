# Connecting Fitbit / Fitbit Air (via the Google Health API)

> **What changed:** In 2026, Google closed new app registration on `dev.fitbit.com`
> ("Registration of new applications via this form is discontinued") and routes all
> new Fitbit + **Fitbit Air** integrations through the **Google Health API**. The
> legacy Fitbit Web API is turned down in **September 2026**. So this integration now
> authenticates with **Google** and reads from `health.googleapis.com`.

The dashboard's **Today's vitals → Fitbit → Connect Fitbit** button uses Google's
OAuth 2.0. The flow runs through small serverless functions in
[`/api/fitbit`](api/fitbit) so your **client secret never touches the browser**.

This only works on a deployed host that runs serverless functions (e.g. **Vercel**).
Opened as a local file, the dashboard falls back to **Apple Watch / Manual** entry.

## One-time setup

1. **Create / pick a Google Cloud project** at <https://console.cloud.google.com>.
2. **Enable the Google Health API**:
   <https://console.developers.google.com/apis/library/health.googleapis.com>
3. **Configure the OAuth consent screen** (External). Add yourself as a **Test user**.
   - ⚠️ The Health API scopes are **Restricted** — in *testing* mode you can authorize
     your own account, but **refresh tokens expire after 7 days**, so you'll re-tap
     *Connect Fitbit* about once a week until you complete Google's verification /
     security review (needed for permanent, public access).
4. **Add the scopes** on the Data Access page
   (<https://console.developers.google.com/auth/scopes>) → *Add or remove scopes* →
   search "Google Health API" → select:
   - `…/auth/googlehealth.sleep.readonly`
   - `…/auth/googlehealth.health_metrics_and_measurements.readonly`
5. **Create an OAuth client ID** → *Web application*. Add an **Authorized redirect URI**
   that matches your deployment exactly:
   - Production: `https://YOUR-APP.vercel.app/api/fitbit/callback`
   - Local (`vercel dev`): `http://localhost:3000/api/fitbit/callback`
6. Copy the **Client ID** and **Client Secret**.
7. In **Vercel → Project → Settings → Environment Variables**, set (the names keep the
   `FITBIT_` prefix so the rest of the suite stays consistent — the *values* are your
   Google Cloud OAuth client credentials):
   - `FITBIT_CLIENT_ID`
   - `FITBIT_CLIENT_SECRET`
8. **Redeploy.** Open the dashboard → **Today's vitals → Fitbit → Connect Fitbit**,
   sign in with the Google account your Fitbit/Fitbit Air syncs to, allow access.

## How it works

| Endpoint | Purpose |
|---|---|
| `GET /api/fitbit/login` | Redirects to Google's consent screen (offline access, CSRF `state`). |
| `GET /api/fitbit/callback` | Exchanges the code for tokens; stores the **refresh token** in an httpOnly cookie. |
| `GET /api/fitbit/data` | Refreshes the access token, reads recent sleep / resting HR / HRV from `health.googleapis.com`, returns vitals. |
| `GET /api/fitbit/logout` | Forgets the stored token (disconnect). |

The returned vitals (HRV, resting HR, sleep hours, sleep efficiency, **bedtime &
wake time**) are written to the suite-wide `patron_health_v1` record, so the
Supplements recommender and the Goals **day-window / estimated-bedtime** feature
pick them up automatically — same as manual or Apple Watch entry, just live.

## What Fitbit does and doesn't give

- ✅ **Sleep** — hours asleep, efficiency, and start/end times (→ bedtime & wake time).
- ✅ **Resting heart rate** and **HRV** (daily RMSSD).
- ❌ **Recovery score** — there's no universal equivalent to WHOOP's recovery, so the
  recovery ring stays blank for Fitbit users. Everything else fills in.

> A forker without the env vars set just sees the **Apple Watch / Manual** options;
> nothing breaks.
