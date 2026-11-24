# Deploying on the student cluster

UK-Transcribe runs as compose stack `ukt` in the university Portainer's
**"Student apps (cluster)"** environment (endpoint 16). You deploy, stop and
start it from your laptop with `deploy/ukt.sh`. The Portainer access token
lives in Doppler and only reaches the script through `doppler run`. It is never
in git, never printed, and not needed by the stack itself.

| | |
|---|---|
| Dashboard | `http://194.171.191.229:30500` (any node answers; campus Wi-Fi or VPN) |
| Services | `ukt-api` (FastAPI + faster-whisper, 6 GiB GPU slice, not published) · `ukt-web` (Next.js, port 30500) |
| Data | volumes `ukt-data` (SQLite + uploads, 20 Gi) and `ukt-models` (Hugging Face cache, 10 Gi) |
| Images | `ghcr.io/alex-krasnoshtanov/uk-transcribe[-web]:sha-<short>`, built by CI on `main` |

## What the cluster is, and what that means here

The environment looks like Docker, but it is Portainer's
[d2k](https://github.com/portainer/d2k): a Docker API over a shared Kubernetes
cluster (namespace `apps`). These rules come from the CEVA and Florensis
projects, which run there too. `tests/test_cluster_compose.py` checks them on
every push.

- **GPU memory, not a GPU.** `BUAS_GPU_MEMORY: "6144"` (MiB) in the API's
  environment gives it a 6 GiB slice of one card. A `deploy.resources.reservations`
  device block would take a whole 48 to 96 GB card that other teams need.
- **The slice is held while the container runs**, even when idle. So stop the
  stack when you don't need it (`ukt.sh stop`) and start it again later; the
  data stays in the volumes.
- **The card is shared** with work outside Kubernetes, so its free memory
  varies. At startup the API checks it. With less than `MIN_FREE_MIB` (5000)
  free, it falls back to the CPU (int8, many times slower). The dashboard's top
  right pill then shows "CPU (slow)". Restart the API later to retry the GPU.
- **Compose stacks via the API only.** "Add stack" in the Portainer UI makes a
  Swarm stack, which gets no GPU and no health checks. `ukt.sh` refuses to
  update such a stack.
- **Public images only, no builds, no bind mounts.** CI pushes to GHCR; the
  packages must be **Public**.
- **Ports 30000–32699 only.** 30500 is ours. `ukt.sh` checks nobody else holds
  it (set `WEB_PORT` to move). Other projects use 32025–32031.
- **Service names are shared DNS names** in the namespace, hence the `ukt-` prefix.
- **New volumes are root-owned**; `ukt.sh` hands them to uid 10001 once with a
  short busybox helper.

## One-time setup (you do these; no secret passes through anyone else)

1. **Doppler.** Run these in the repo folder:
   ```powershell
   doppler login
   doppler projects create uk-transcribe
   doppler setup --project uk-transcribe --config prd
   ```
   `doppler setup` only records the project and config for this folder; no
   secret is written to disk.
2. **Portainer token.** In Portainer, open My account, then Access tokens,
   then Add access token, named `uk-transcribe-deploy`. Then store it and the
   three non-secret values:
   ```powershell
   doppler secrets set PORTAINER_TOKEN
   doppler secrets set PORTAINER_URL="https://194.171.191.226:3085" PORTAINER_ENDPOINT_ID="16" PORTAINER_INSECURE="1"
   ```
   The first command prompts for the value, so it never lands in your shell
   history.
3. **Images.** Merge to `main` and wait for the "Images" workflow. Then make
   both packages Public, once: github.com/alex-krasnoshtanov, then Packages,
   then `uk-transcribe` (and then `uk-transcribe-web`), then Package
   settings, then Change visibility, then Public.
4. **Tools.** `curl` and `jq` in Git Bash (`winget install jqlang.jq`).

## Day to day

Run these on campus Wi-Fi or the VPN, from the repo folder:

```bash
doppler run -- bash deploy/ukt.sh deploy            # latest main (sha-<short> of origin/main)
doppler run -- bash deploy/ukt.sh deploy sha-1a2b3c4 # a specific build (also how you roll back)
doppler run -- bash deploy/ukt.sh stop              # frees the GPU slice
doppler run -- bash deploy/ukt.sh start             # same data, model already cached
doppler run -- bash deploy/ukt.sh status            # containers, health, device, free VRAM
doppler run -- bash deploy/ukt.sh logs ukt-api 300
```

The first deploy pulls a ~4 GB CUDA image and downloads `large-v3` (~3 GB) into
`ukt-models`, which can take 10–20 minutes. Later starts take about a minute.

## Sizing the GPU slice

`large-v3`, float16, beam 5 is estimated at about 4–5 GiB of VRAM at its peak,
so the stack asks for 6 GiB. Measure it after the first real transcription:

1. Start a long file. In Portainer, open `ukt-api`, then Console, and run
   `nvidia-smi --query-compute-apps=pid,used_memory --format=csv`. Or watch
   `gpu_free_mib` from `ukt.sh status` before and during a job.
2. Set `BUAS_GPU_MEMORY` to the measured peak plus about 1 GiB, and keep
   `MIN_FREE_MIB` at or below it. Then commit and run `ukt.sh deploy`.

CTranslate2 cannot cap its own memory, so the slice is a reservation, not a
hard limit. Don't add a second worker or raise `BEAM_SIZE` without measuring
again.

## Backup and restore

There is no host access, and `docker cp` doesn't work on d2k. Back up the
`ukt-data` volume with a short-lived helper, as CEVA's `infra/README.md`
describes:

1. `ukt.sh stop` (a volume takes one container at a time).
2. In Portainer, Student apps (cluster), add a container:
   - Image `busybox:1.37`, user `0:0`, restart `unless-stopped`.
   - Volume `ukt-data` on `/d`, read-only. Port `30501` → `8000`.
   - Environment `PW` (a long random password) and `FILE` (a random name
     ending in `.tar`).
   - Command:
     `sh -c 'mkdir /srv && tar cf "/srv/$FILE" -C /d . && printf "/:ukt:%s\n" "$PW" > /tmp/httpd.conf && exec httpd -f -p 8000 -h /srv -c /tmp/httpd.conf'`
3. `curl -u "ukt:<PW>" -o ukt-data.tar "http://194.171.191.229:30501/<FILE>"`
4. Remove the helper, then `ukt.sh start`.

To restore, reverse it: serve the tar from a machine the cluster can reach,
`wget` it into the volume from a helper, then run `chown -R 10001:10001`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Portainer is unreachable` | not on campus Wi-Fi or VPN |
| `refused the token (HTTP 401)` | the token expired or was deleted: make a new one, `doppler secrets set PORTAINER_TOKEN` |
| `is not pullable anonymously` | CI hasn't finished, or the package is still private |
| `port 30500 is used by …` | another team took it: `WEB_PORT=30510 doppler run -- bash deploy/ukt.sh deploy` |
| pill says `CPU (slow)` | the card had < 5 GB free at startup: `ukt.sh stop`, then `ukt.sh start` later |
| `is a Swarm stack` | someone created `ukt` in the UI: remove it in Portainer, deploy again |
| api stays `starting` for a long time | the first model download; `ukt.sh logs ukt-api` |
