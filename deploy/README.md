# Deploying on the student cluster

UK-Transcribe runs as compose stack `ukt` in the university Portainer's
**"Student apps (cluster)"** environment (endpoint 16). You run it from GitHub:

- **Every push to `main`** builds both images, smoke-tests them, pushes them to
  GHCR, and deploys them (workflow "Images").
- **Actions, then "Operate the cluster", then Run workflow** lets you pick
  `status`, `deploy` (optionally a `sha-<short>` tag, which is also how you roll
  back), `stop` (frees the GPU slice), `start`, `logs-api` or `logs-web`. The
  result and the dashboard URL appear in the run's summary.

Both workflows run their cluster step on a small self-hosted runner *inside*
the cluster (`deploy/runner.yml`), because Portainer is only reachable from the
university network. The Portainer token stays in Doppler. GitHub holds only a
read-only Doppler service token, and `deploy/ukt.sh` reads the Portainer values
from it at run time. They are never in git and never printed (masked in logs).

| | |
|---|---|
| Dashboard | `http://194.171.191.229:31847` (any node answers; campus Wi-Fi or VPN) |
| Services | `ukt-api` (FastAPI + faster-whisper, 6 GiB GPU slice, not published) · `ukt-web` (Next.js, port 31847) |
| Data | volumes `ukt-data` (SQLite + uploads, 20 Gi) and `ukt-models` (Hugging Face cache, 10 Gi) |
| Images | `ghcr.io/alex-krasnoshtanov/uk-transcribe[-web]:sha-<short>`, built by CI on `main` |
| Runner | stack `ukt-runner`, runner `uni-apps-ukt` (no ports, 1 CPU / 512 MB) |

## What the cluster is, and what that means here

The environment looks like Docker, but it is Portainer's
[d2k](https://github.com/portainer/d2k): a Docker API over a shared Kubernetes
cluster (namespace `apps`). These rules come from the CEVA and Florensis
projects, which run there too. `tests/test_cluster_compose.py` checks them on
every push.

- **GPU memory, not a GPU.** `BUAS_GPU_MEMORY: "6144"` (MiB) in the API's
  environment gives it a 6 GiB slice of one card. A `deploy.resources.reservations`
  device block would take a whole 48 to 96 GB card that other teams need.
- **The slice is held while the container runs**, even when idle. So run
  `stop` when you don't need it and `start` later; the data stays in the
  volumes.
- **The card is shared** with work outside Kubernetes, so its free memory
  varies. At startup the API checks it. With less than `MIN_FREE_MIB` (5000)
  free, it falls back to the CPU (int8, many times slower). The dashboard's top
  right pill then shows "CPU (slow)". Run `stop` and `start` later to retry
  the GPU.
- **Compose stacks via the API only.** "Add stack" in the Portainer UI makes a
  Swarm stack, which gets no GPU and no health checks. `ukt.sh` refuses to
  update such a stack. (The runner is the exception: it needs neither.)
- **Public images only, no builds, no bind mounts.** CI pushes to GHCR; the
  packages must be **Public**.
- **Ports 30000–32699 only, and shared with the whole cluster.** They are
  NodePorts, so services in other namespaces hold some that this account
  cannot list. 30500, for example, was taken. 31847 is ours. Before each
  deploy, `deploy` checks from the network that nothing else answers on it, and
  if something does, it suggests free ports. To move, set the repository
  variable `WEB_PORT`.
- **Service names are shared DNS names** in the namespace, hence the `ukt-` prefix.
- **New volumes are root-owned**; `deploy` hands them to uid 10001 once with a
  short busybox helper.

## One-time setup (you do these; no secret passes through anyone else)

1. **Doppler project with the Portainer values.**
   - Create a Portainer access token: Portainer, then My account, then Access
     tokens, then Add access token, named `uk-transcribe-deploy`.
   - Then, in PowerShell in the repo folder:
     ```powershell
     doppler login
     doppler projects create uk-transcribe
     doppler setup --project uk-transcribe --config prd
     doppler secrets set PORTAINER_TOKEN
     doppler secrets set PORTAINER_URL="https://194.171.191.226:3085" PORTAINER_ENDPOINT_ID="16" PORTAINER_INSECURE="1"
     ```
   - `doppler secrets set PORTAINER_TOKEN` prompts for the value, so it never
     lands in your shell history.
2. **Read-only service token for GitHub.** Doppler, then `uk-transcribe`, then
   `prd`, then Access, then Generate service token (read access) named
   `github-actions`. Then:
   ```powershell
   gh secret set DOPPLER_TOKEN -R alex-krasnoshtanov/UK-Transcribe
   ```
   This also prompts for the value. It is a repository secret, not an
   environment secret, because the deploy step runs in a called workflow.
3. **Give CI write access to the old `uk-transcribe` package.** The package
   was pushed by hand once, so it isn't linked to this repo and CI gets
   `permission_denied: write_package`. To fix it:
   - On github.com/alex-krasnoshtanov, open Packages, then `uk-transcribe`,
     then Package settings.
   - Under "Manage Actions access", click Add repository, choose `UK-Transcribe`
     and set the role to **Write**.
   - Re-run the failed "Images" run. `uk-transcribe-web` is new, so CI creates
     and links it itself.
4. **Make both packages Public**, once, after they exist: open each package's
   Package settings, then Change visibility, then Public. d2k cannot pull
   private images.
5. **The runner.**
   - Make a PAT that can register runners on this repo:
     - fine-grained: repository `UK-Transcribe`, permission "Administration:
       read and write";
     - or classic: the `repo` scope.
   - In Portainer, open Student apps (cluster), then Stacks, then Add stack
     `ukt-runner`. In the Web editor, paste `deploy/runner.yml`.
   - Add the environment variable `GH_RUNNER_PAT` with the PAT, then Deploy.
   - GitHub, then Settings, then Actions, then Runners should list
     `uni-apps-ukt` as Idle.
6. **Turn on auto-deploy:**
   ```powershell
   gh variable set CLUSTER_DEPLOY --body true -R alex-krasnoshtanov/UK-Transcribe
   ```
   Without it, the Images workflow only builds and pushes. You can still
   deploy by hand from "Operate the cluster".

The first deploy pulls a ~4 GB CUDA image and downloads `large-v3` (~3 GB) into
`ukt-models`, which can take 10–20 minutes. Later starts take about a minute.

## From your laptop instead (optional)

The same script runs locally on campus Wi-Fi or the VPN. In PowerShell, use the
wrapper. Plain `bash` there is WSL's, which fails without a Linux distro
(`execvpe(/bin/bash) failed`). The wrapper uses Git Bash and `doppler run`:

```powershell
.\deploy\ukt.ps1 status
.\deploy\ukt.ps1 deploy sha-1a2b3c4
.\deploy\ukt.ps1 stop
.\deploy\ukt.ps1 start
.\deploy\ukt.ps1 logs ukt-api 300
```

It needs `jq` in Git Bash (`winget install jqlang.jq`).

## Sizing the GPU slice

`large-v3`, float16, beam 5 is estimated at about 4–5 GiB of VRAM at its peak,
so the stack asks for 6 GiB. Measure it after the first real transcription:

1. Start a long file. In Portainer, open `ukt-api`, then Console, and run
   `nvidia-smi --query-compute-apps=pid,used_memory --format=csv`. Or compare
   `gpu_free_mib` in a `status` run before and during a job.
2. Set `BUAS_GPU_MEMORY` in `deploy/cluster.yml` to the measured peak plus
   about 1 GiB, and keep `MIN_FREE_MIB` at or below it. Push to `main`; it
   deploys itself.

CTranslate2 cannot cap its own memory, so the slice is a reservation, not a
hard limit. Don't add a second worker or raise `BEAM_SIZE` without measuring
again.

## Backup and restore

There is no host access, and `docker cp` doesn't work on d2k. Back up the
`ukt-data` volume with a short-lived helper, as CEVA's `infra/README.md`
describes:

1. Run `stop` (a volume takes one container at a time).
2. In Portainer, Student apps (cluster), add a container:
   - Image `busybox:1.37`, user `0:0`, restart `unless-stopped`.
   - Volume `ukt-data` on `/d`, read-only. Port `31848` → `8000`.
   - Environment `PW` (a long random password) and `FILE` (a random name
     ending in `.tar`).
   - Command:
     `sh -c 'mkdir /srv && tar cf "/srv/$FILE" -C /d . && printf "/:ukt:%s\n" "$PW" > /tmp/httpd.conf && exec httpd -f -p 8000 -h /srv -c /tmp/httpd.conf'`
3. `curl -u "ukt:<PW>" -o ukt-data.tar "http://194.171.191.229:31848/<FILE>"`
4. Remove the helper, then run `start`.

To restore, reverse it: serve the tar from a machine the cluster can reach,
`wget` it into the volume from a helper, then run `chown -R 10001:10001`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Operate the cluster" stays queued | the runner is down: Portainer, then `ukt-runner`, then logs; GitHub, then Settings, then Runners |
| `Doppler refused DOPPLER_TOKEN` | the service token was revoked: make a new one, `gh secret set DOPPLER_TOKEN` |
| `refused the token (HTTP 401)` | the Portainer token expired: make a new one, `doppler secrets set PORTAINER_TOKEN` |
| `permission_denied: write_package` | step 3 of the setup (package not linked to the repo) |
| `is not pullable anonymously` | the Images run hasn't finished, or the package is still private |
| `port 31847 is already used` / `provided port is already allocated` | taken outside our stack: set the repository variable `WEB_PORT` to one of the free ports the error lists |
| pill says `CPU (slow)` | the card had < 5 GB free at startup: `stop`, then `start` later |
| `is a Swarm stack` | someone created `ukt` in the UI: remove it in Portainer, deploy again |
| api stays `starting` for a long time | the first model download; run `logs-api` |
| "network error during upload" | the dashboard dropped the upload: check `ukt-web`'s restart count (memory) and logs. The proxy streams with backpressure (~110 MB for a 900 MB file) and allows 3 h per upload (`UPLOAD_TIMEOUT_S`) |
| `execvpe(/bin/bash) failed` locally | that is WSL's bash: use `.\deploy\ukt.ps1` |
