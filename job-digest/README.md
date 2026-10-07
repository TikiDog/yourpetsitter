# Job Digest

Once a day, this app searches several job sources, removes duplicates, and
emails you a digest of new listings.

- **Keywords:** InDesign, production artist, graphic designer, UI designer, ePub
- **Locations:** Remote (US), and Sacramento, CA within 25 miles
- **Time window:** posted in the last 24 hours
- **Schedule:** every morning at about 7:15 AM Pacific, via GitHub Actions (free)

It uses official APIs and RSS feeds only. It never scrapes LinkedIn, Indeed
or Glassdoor. Listings from those sites come in through JSearch, which reads
them from Google for Jobs.

| Source | What it covers | Key needed? |
|---|---|---|
| JSearch (RapidAPI) | LinkedIn, Indeed, Glassdoor, ZipRecruiter, company career sites | Yes, free tier |
| Adzuna | Large US job aggregator | Yes, free |
| Remotive | Remote jobs | No |
| We Work Remotely | Remote design jobs (RSS) | No |
| USAJOBS | Federal jobs near Sacramento and remote federal jobs | Yes, free |

**Delivery:** if the Gmail login (Step 1 below) is set up, the digest
arrives as a formatted email. Until then, the app posts each day's digest
as a GitHub **issue** in this repo, assigned to you. GitHub emails you
about it, and yesterday's digest issue closes automatically. The repo is
public, so those issues are too. Job listings are public anyway, but adding
the Gmail login moves delivery to your private inbox.

Every source is optional. If a key is missing, the digest skips that source,
runs the rest, and says so at the bottom of the email. Remotive and We Work
Remotely work without keys, so the app runs as soon as email is set up.

---

## Setup, step by step

You will collect a few keys, save them as GitHub *secrets* (encrypted
settings that only the workflow can read), and then turn the schedule on.
Plan on about 20 minutes. USAJOBS emails your key, which can take a little
while to arrive.

### Step 1: Gmail app password (required, used to send the email)

Gmail won't accept your normal password from an app. It needs an *app password* instead.

1. Go to <https://myaccount.google.com/security>.
2. Under **How you sign in to Google**, turn on **2-Step Verification** if it
   isn't on already. App passwords are only available once it's on.
3. Go to <https://myaccount.google.com/apppasswords>. You can also search
   "App passwords" in your Google Account.
4. Type a name such as `Job digest` and click **Create**.
5. Google shows a 16-character password. Copy it now, because Google
   won't show it again. Ignore the spaces.

You'll save it as `SMTP_PASSWORD` in Step 5.

### Step 2: JSearch API key (RapidAPI)

1. Go to <https://rapidapi.com> and click **Sign Up**. Signing in with
   Google works.
2. Open the JSearch page: <https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch>.
   You can also search **JSearch** on RapidAPI. Pick the API published by
   *OpenWeb Ninja / letscrape*.
3. Click **Pricing** and subscribe to the free **Basic** plan. RapidAPI may
   ask for a card even on the free plan. The free plan has a monthly
   request limit. This app uses **2 requests a day**, or about 60 a month.
4. Click **Endpoints**. In the code panel on the right, find the
   `X-RapidAPI-Key` header and copy its value. It's a long string of letters
   and numbers. It's also under **My Apps → default-application →
   Authorization**.

You'll save it as `RAPIDAPI_KEY`.

### Step 3: Adzuna app ID and key

1. Go to <https://developer.adzuna.com> and click **Register**.
2. Fill in the form. For the website or app, you can put your GitHub repo
   URL and describe it as a "personal job alert".
3. Confirm your email, then sign in. The **Dashboard → API Access Details**
   page shows an **Application ID** (short) and an **Application Key** (long).

You'll save them as `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. The app makes
about 10 Adzuna requests a day, which is well inside the free limits.

### Step 4: USAJOBS API key

1. Go to <https://developer.usajobs.gov/apirequest/>.
2. Enter your name and email and describe the use, for example "Personal daily
   job alert for design jobs near Sacramento".
3. USAJOBS emails you an **API key**.

You'll save the key as `USAJOBS_API_KEY`. Save the **same email address you
registered with** as `USAJOBS_EMAIL`. USAJOBS requires both.

### Step 5: Save the secrets in GitHub

1. Open this repository on GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Click **New repository secret** for each row below. Names must match exactly.

| Secret name | Value |
|---|---|
| `SMTP_USER` | Your Gmail address (sends the digest) |
| `SMTP_PASSWORD` | The 16-character app password from Step 1 |
| `EMAIL_TO` | Where to deliver the digest. Optional: leave it out to send to `SMTP_USER` |
| `RAPIDAPI_KEY` | From Step 2 |
| `ADZUNA_APP_ID` | From Step 3 |
| `ADZUNA_APP_KEY` | From Step 3 |
| `USAJOBS_API_KEY` | From Step 4 |
| `USAJOBS_EMAIL` | The email you registered with USAJOBS |

Secrets stay encrypted. They are never shown in logs or visible to people
who view the repo.

### Step 6: Turn on the schedule

GitHub only runs scheduled workflows from the repository's **default branch**
(`main`). Merge the pull request that adds this app into `main`. After that:

1. Go to the **Actions** tab. If GitHub asks, click **I understand my
   workflows, go ahead and enable them**.
2. Click **Daily job digest** in the left column, then **Run workflow**.
   This sends a digest right away so you can check that everything works.
3. Check your inbox. A spam check is a good idea the first time. If a source
   failed or was skipped, the reason appears at the bottom of the email.

After that, it runs by itself every morning.

---

## How it works

1. **Fetch.** Each source is searched for each keyword, both near Sacramento
   (25-mile radius) and remote.
2. **Filter.**
   - Role phrases (*production artist*, *graphic designer*, *UI designer*) must
     appear in the **job title**. Small variations count, such as "Graphic
     Design Specialist", "Sr. UI/UX Designer", or "Visual Information
     Specialist (Graphic Designer)".
   - Software names (*InDesign*, *ePub*) can appear in the title **or** the
     description. That way, a "Layout Specialist" job that requires InDesign
     still shows up.
   - Remote jobs that are limited to other countries, such as "Europe only",
     are left out.
   - Jobs posted more than 24 hours ago are left out.
3. **Remove duplicates.** Two listings count as the same job when the company
   and the title match after normalizing:
   - case and punctuation (`Acme, Inc.` = `ACME`)
   - abbreviations (`Sr.` = `Senior`, `&` = `and`, `UI/UX` = `UX/UI`)
   - noise like `(Remote)`, `- Hybrid`, or `| Sacramento, CA`
   - minor wording differences, using word-overlap matching

   Different levels are always kept apart: "Graphic Designer", "Senior
   Graphic Designer" and "Graphic Designer II" are three separate jobs.
4. **Pick the best link.** For each job, the employer's own careers page wins.
   That means the company's website or its applicant-tracking system, such as
   Greenhouse, Lever, Workday, Ashby or iCIMS. Aggregators like LinkedIn and
   Indeed rank last. Jobs that link to the employer's page get a green
   **Company site** badge. Other places the job is listed appear under "Also
   listed on".
5. **Skip jobs you've already seen.** The app remembers what it has emailed
   for 30 days, so the same job is never sent twice, even if it shows up on
   a different site the next day.

**Why a few sources look back further than 24 hours:** Remotive's free API
only publishes listings about a day after they're posted, and USAJOBS dates
are calendar days rather than exact times. Those two sources look back 72 and
48 hours. The "already seen" memory keeps their jobs from repeating, so every
job in a digest is still new to you.

## Changing the search

Edit `job-digest/config.json`:

- `keywords`: add or remove search terms.
- `local_search`: change the city, state or `radius_miles`.
- `include_remote_us`: set to `false` to only get local jobs.
- `lookback_hours`: the time window, 24 by default.
- `send_when_empty`: set to `false` to skip the email on days with no new jobs.
- `sources`: set any source to `false` to turn it off.

To change the time of day, edit the `cron` line in
`.github/workflows/job-digest.yml`. Cron times are in UTC. Sacramento is
UTC−7 in summer and UTC−8 in winter.

## Running it on your own computer (optional)

You need Python 3.10 or newer. No other packages are required.

```bash
cd job-digest
export SMTP_USER=you@gmail.com SMTP_PASSWORD=xxxxxxxxxxxxxxxx
# ...plus any API keys you have

python -m jobdigest --dry-run --preview preview.html   # no email; open preview.html
python -m jobdigest                                    # send for real
python -m unittest discover -s tests                   # run the tests
```

## Good to know

- **Cost:** free. GitHub Actions is free for this amount of use, and every
  API is used within its free tier.
- **Inactive repos:** in a *public* repo, GitHub pauses scheduled workflows
  after 60 days with no commits. GitHub emails you first, and one click on
  the Actions tab turns it back on.
- **Using another email provider:** set `SMTP_HOST` and `SMTP_PORT` (for
  example `smtp.office365.com` and `587`) as extra secrets, and add them to
  the `env:` list in the workflow file.
