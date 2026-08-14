# Deploying ETAP Lens

Static frontend on Vercel, Flask API on Cloud Run, uploads in a GCS bucket.

```
etaplens.mazin.ltd       Vercel      web/ - index.html, app.js, style.css, config.js
api.etaplens.mazin.ltd   Cloud Run   Flask API, ETAP_LENS_MODE=hosted
                         GCS         uploads/<session>/... and derived caches
browser ──signed PUT───► GCS         the file never passes through the API
```

The direct-to-bucket upload is not an optimisation. Cloud Run caps request
bodies at **32 MB** and a study result is routinely five times that, so the
browser has to PUT straight to GCS and then tell the API the object landed.

Everything below needs your own credentials. The application side is done and
tested; this is the part only you can run.

---

## 1. Project and APIs

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com storage.googleapis.com
```

## 2. Bucket

Pick the region deliberately — it is where other people's engineering models
will sit at rest, and the answer may be governed by your client agreements.

```bash
gcloud storage buckets create gs://etaplens-uploads \
  --location=us-central1 --uniform-bucket-level-access
```

Apply the CORS policy so the browser can PUT to it, and the lifecycle rules so
storage cannot grow without bound:

```bash
gcloud storage buckets update gs://etaplens-uploads --cors-file=deploy/bucket-cors.json
```

```bash
gcloud storage buckets update gs://etaplens-uploads --lifecycle-file=deploy/bucket-lifecycle.json
```

Edit `deploy/bucket-cors.json` first if your frontend origin differs. **A
wrong origin here is the single most likely reason uploads fail** — the API
call succeeds, the PUT is blocked by the browser, and nothing in the server
logs shows it.

## 3. Service account

Signing URLs requires the service account's own key, which means the
`iam.serviceAccountTokenCreator` role on itself:

```bash
gcloud iam service-accounts create etap-lens-api --display-name="ETAP Lens API"
```

```bash
gcloud storage buckets add-iam-policy-binding gs://etaplens-uploads \
  --member=serviceAccount:etap-lens-api@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --role=roles/storage.objectAdmin
```

```bash
gcloud iam service-accounts add-iam-policy-binding \
  etap-lens-api@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --member=serviceAccount:etap-lens-api@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --role=roles/iam.serviceAccountTokenCreator
```

## 4. Artifact Registry and first deploy

```bash
gcloud artifacts repositories create etap-lens --repository-format=docker --location=us-central1
```

```bash
gcloud builds submit --config=deploy/cloudbuild.yaml --substitutions=_BUCKET=etaplens-uploads,_CORS_ORIGINS=https://etaplens.mazin.ltd
```

Then attach the service account (the build does not set it):

```bash
gcloud run services update etap-lens-api --region=us-central1 --service-account=etap-lens-api@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

Check it came up in the right mode:

```bash
curl -s https://YOUR-SERVICE-URL.run.app/api/config
```

You want `"deploy_mode":"hosted"`, `"local_filesystem":false`,
`"require_session":true`. If `local_filesystem` is `true`, the container is
running with the desktop defaults and **`/api/browse` will list the
container's filesystem to anyone who asks** — fix that before going further.

## 5. Domain

```bash
gcloud run domain-mappings create --service=etap-lens-api --domain=api.etaplens.mazin.ltd --region=us-central1
```

Add the CNAME it prints to your DNS.

## 6. Turnstile

Create a widget at <https://dash.cloudflare.com> for `etaplens.mazin.ltd`,
then store the secret and point the service at it:

```bash
printf 'YOUR_SECRET_KEY' | gcloud secrets create etap-lens-turnstile --data-file=-
```

```bash
gcloud run services update etap-lens-api --region=us-central1 --update-secrets=ETAP_LENS_TURNSTILE_SECRET_KEY=etap-lens-turnstile:latest --update-env-vars=ETAP_LENS_TURNSTILE_SITE_KEY=YOUR_SITE_KEY
```

The frontend mounts the widget only when the API reports a site key, so until
this step it simply runs without bot protection.

## 7. Frontend on Vercel

Copy `deploy/vercel.json` to the repo root, set `web/config.js` to point at
the API, and deploy with `web` as the output directory.

```js
window.ETAP_API_BASE = 'https://api.etaplens.mazin.ltd';
```

Two things to keep straight:

- The CSP in `vercel.json` names `api.etaplens.mazin.ltd` and
  `storage.googleapis.com` in `connect-src`. Change the API host and you must
  change that too, or every request is blocked with no useful error.
- Vercel preview deployments get generated hostnames. Either accept that
  previews cannot call the API, or set `ETAP_LENS_CORS_ORIGIN_REGEX` to
  something anchored to your own project, e.g.
  `^https://etaplens-[a-z0-9-]+\.vercel\.app$`. Do **not** use
  `.*\.vercel\.app` — that lets anybody's Vercel app call your API from a
  visitor's browser.

## 8. Cost control

Set a budget with alerts before announcing the URL anywhere. The controls that
actually bound spend, in order of effectiveness:

| Control | Where |
|---|---|
| `--max-instances=1` | already in `cloudbuild.yaml` |
| Upload size cap enforced by the signed URL | `ETAP_LENS_MAX_UPLOAD_MB` |
| Turnstile on the upload-URL endpoint | step 6 |
| Bucket lifecycle deletion | step 2 |
| Lite cache — 3.4 MB per study instead of 272 MB | on by default when hosted |

---

## Known limits

**Single instance.** Load-job progress lives in an in-memory dict, so
`--max-instances=1` is load-bearing, not a cost tweak. Raising it means moving
that state to Firestore first, or clients will poll an instance that has never
heard of their job.

**Per-session quotas are best-effort.** The counters are derived from what is
on the instance's disk, so they reset when Cloud Run recycles it. The real
ceilings are the upload size cap, Turnstile, and max-instances.

**Sessions are anonymous bearer tokens.** A 128-bit id in `localStorage`
identifies a visitor's uploads. Clearing site data loses access to them; there
is no recovery, by design. Anyone who obtains the id can read that session's
projects, which is why it is random rather than sequential and why ownership
is checked on every read rather than inferred from the id being hard to guess.

**Uploaded models are other people's data.** Even with lite mode discarding
the per-step detail and lifecycle rules deleting caches after a week, running
this publicly makes you a processor of whatever people upload. Put a short
privacy note on the page saying what is stored and for how long.
