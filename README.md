# Concept2 Personal Dashboard

A Streamlit dashboard for your Concept2 rowing machine training log, powered by the [Concept2 Logbook API](https://log.concept2.com/developers/documentation/).

## Quick Start (sample data)

```bash
pip install -r requirements.txt
streamlit run app.py
```

The dashboard runs immediately with 65 realistic sample workouts so you can explore the UI before connecting your account.

---

## Connecting your real data

### 1. Register your application

1. Log in to [log.concept2.com](https://log.concept2.com)
2. Go to **Account → API Access** (or visit https://log.concept2.com/developers)
3. Click **Register Application**
4. Fill in the form:
   - **Application Name**: anything you like (e.g. "My Dashboard")
   - **Redirect URI**: `http://localhost` (for a personal script you can use a loopback URI)
5. Note your **Client ID** and **Client Secret**

### 2. Complete the OAuth2 flow

The Concept2 API uses OAuth2 Authorization Code flow.  For a personal dashboard the simplest approach is to use a one-time script to obtain a token:

```python
import requests

CLIENT_ID     = "your_client_id"
CLIENT_SECRET = "your_client_secret"
REDIRECT_URI  = "http://localhost"

# Step 1 — send the user to the authorization URL
auth_url = (
    f"https://log.concept2.com/oauth/authorize"
    f"?client_id={CLIENT_ID}"
    f"&response_type=code"
    f"&redirect_uri={REDIRECT_URI}"
    f"&scope=results:read"
)
print("Open this URL and authorize the app:")
print(auth_url)

# Step 2 — paste the `code` from the redirect URL
code = input("Paste the code from the redirect URL: ").strip()

# Step 3 — exchange the code for a token
resp = requests.post("https://log.concept2.com/oauth/access_token", data={
    "client_id":     CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code":          code,
    "grant_type":    "authorization_code",
    "redirect_uri":  REDIRECT_URI,
})
resp.raise_for_status()
token_data = resp.json()
print("Access token:", token_data["access_token"])
```

### 3. Add the token to config.py

Open `config.py` and replace the placeholder:

```python
# Before
API_TOKEN = "YOUR_TOKEN_HERE"

# After
API_TOKEN = "eyJ0eXAiOiJKV1Qi..."   # your real token
```

Restart the Streamlit app — the warning banner will disappear and your real workouts will load.

> **Token expiry**: Concept2 access tokens expire after 30 days. Repeat step 2 to refresh. The API also returns a `refresh_token` you can use to automate renewal — see the [OAuth2 docs](https://log.concept2.com/developers/documentation/#oauth-refresh).

---

## Project structure

```
concept2-dashboard/
├── app.py          # Streamlit UI — all six dashboard sections
├── api.py          # Concept2 API client (falls back to sample data)
├── data.py         # Sample generation, DataFrame builder, stats helpers
├── config.py       # Token and constants
└── requirements.txt
```

## Dashboard sections

| # | Section | Description |
|---|---------|-------------|
| 1 | **Summary bar** | Total meters, workouts, time; this month, this year, streak |
| 2 | **Recent workouts** | Last 20 workouts; click to drill down |
| 3 | **Progress charts** | Meters/week, pace trend (filterable), SPM, heart rate |
| 4 | **Personal Records** | Best times for all standard C2 distances + timed events |
| 5 | **Workout detail** | Header stats + splits table + pace-per-split bar chart |
| 6 | **Challenges** | Progress bars for active Concept2 challenges |

## Key formulas

- **Pace** (s/500m): `(time_s / distance_m) × 500`
- **Watts**: `2.80 / pace_s³ × 500³`
- **Display pace**: `M:SS.s` (e.g. `2:05.3`)
