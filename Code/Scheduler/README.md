# CustomerAgent Investigation Scheduler

A small Python script that runs as an **Azure Container Apps Job** on a
cron schedule (default `*/5 * * * *` = every 5 minutes). Each run acquires
an Entra token via `DefaultAzureCredential` and POSTs to Manik's
CustomerAgent endpoint to kick off an investigation for the configured
customer and rolling time window.

This component is **fully separate** from `Code/CustomerAgent/src/`
(Manik's domain). The only contract is the HTTP POST.

## What it does each tick

1. Computes `end_time = utcnow()` and `start_time = end_time − LOOKBACK_MINUTES`.
2. Acquires a bearer token for `AUDIENCE_SCOPE` via the Job's
   system-assigned managed identity (locally: `az login`).
3. POSTs `{customer_name, start_time, end_time}` to `ENDPOINT_URL`.
4. Logs the returned services + xcvs to stdout (visible in Container Apps
   "Job executions" → "Console logs").

## Environment variables

| Name | Default | Description |
|---|---|---|
| `CUSTOMER_NAME` | `BlackRock, Inc` | Investigation target |
| `LOOKBACK_MINUTES` | `60` | Time window (end = utcnow; start = end − N) |
| `ENDPOINT_URL` | `https://ca-ratio-customeragent-dev.…/api/run/services` | Cloud target |
| `AUDIENCE_SCOPE` | `de5f2e0f-ac6d-418e-a64c-e38dbbd116e5/.default` | Token audience |
| `HTTP_TIMEOUT` | `120` | Seconds |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

## Local development

```powershell
# From the repo root
cd Code\Scheduler

# Use the root .venv for simplicity (already has azure-identity, httpx)
..\..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt   # idempotent if already installed

# Make sure you're logged in to the right tenant + subscription
az login --tenant 72f988bf-86f1-41af-91ab-2d7cd011db47
az account set --subscription 01819f01-7af1-4dd8-9354-9dccc163ceae

# Smoke-run with defaults (BlackRock, last hour)
python run_investigation.py

# Override per-run
$env:CUSTOMER_NAME="Contoso Capital"; $env:LOOKBACK_MINUTES=120
python run_investigation.py
Remove-Item Env:CUSTOMER_NAME, Env:LOOKBACK_MINUTES
```

Expected output:

```
… scheduler.run.start customer='BlackRock, Inc' lookback_min=60 window=[…, …] endpoint=https://…
… scheduler.run.success customer='BlackRock, Inc' services=2 results=[{"service_name":"SQL Connectivity","xcv":"…",…}, …]
```

## Deploy

```powershell
# 1. Build & push the image
$tag = "dev-$(git rev-parse --short HEAD)"
az acr build -t "customeragent-scheduler:$tag" -r <acr-name> .

# 2. Deploy the Job (Bicep defined in deploy/main.bicep)
az deployment group create `
  -g rg-ratio-ai-dev `
  -f ..\..\deploy\main.bicep `
  -p schedulerImageTag=$tag

# 3. Verify the schedule
az containerapp job show -n caj-customeragent-scheduler -g rg-ratio-ai-dev `
  --query "properties.configuration.scheduleTriggerConfig"
```

## Manual ad-hoc trigger (overrides defaults without changing the schedule)

```powershell
az containerapp job start `
  -n caj-customeragent-scheduler -g rg-ratio-ai-dev `
  --env-vars CUSTOMER_NAME="Contoso Capital" LOOKBACK_MINUTES=120
```

## Inspect executions

```powershell
# List recent runs
az containerapp job execution list -n caj-customeragent-scheduler -g rg-ratio-ai-dev -o table

# Tail logs of the latest run
$last = az containerapp job execution list -n caj-customeragent-scheduler -g rg-ratio-ai-dev `
  --query "[0].name" -o tsv
az containerapp job logs show -n caj-customeragent-scheduler -g rg-ratio-ai-dev `
  --container scheduler --execution $last
```

Or via the portal: **Container Apps** → `caj-customeragent-scheduler` →
**Execution history** → click any execution for logs.

## Permissions Manik needs to grant (one-time)

The Job's system-assigned managed identity must be allowed to acquire a
token for the CustomerAgent app reg's audience
(`de5f2e0f-ac6d-418e-a64c-e38dbbd116e5`). After the first deploy:

```powershell
# 1. Get the Job's managed identity principal id
$miId = az containerapp job show `
  -n caj-customeragent-scheduler -g rg-ratio-ai-dev `
  --query "identity.principalId" -o tsv

# 2. Send $miId to Manik. He adds it as an authorized client of the
#    de5f2e0f-... Entra app reg (Expose an API → Authorized client
#    applications, OR App roles → assign the MI to a calling role).
```

Until that grant lands, the Job will log
`scheduler.token.failed audience=de5f2e0f-…` and exit 2. That's expected.

## Cron format

`*/5 * * * *` (every 5 min). Standard Linux cron with minute precision.
Tweak the `schedulerCron` Bicep parameter and re-deploy to change.

## Cost

Consumption Container Apps pricing — ≈ 8.6 k executions/month × 30 s × 0.25 vCPU / 0.5 GiB ≈ **$1–3/month**.
