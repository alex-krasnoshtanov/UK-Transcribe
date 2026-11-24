#!/usr/bin/env bash
# Run UK-Transcribe on the BUas "Student apps (cluster)" through the Portainer API.
#
#   doppler run -- bash deploy/ukt.sh deploy [sha-<short>]   create or update the stack
#   doppler run -- bash deploy/ukt.sh stop                   stop it (frees the GPU slice)
#   doppler run -- bash deploy/ukt.sh start                  start it again (data is kept)
#   doppler run -- bash deploy/ukt.sh status                 containers, health, /healthz
#   doppler run -- bash deploy/ukt.sh logs [ukt-api|ukt-web] [lines]
#
# Needs bash, curl and jq (Git Bash on Windows is fine) and the university
# network (campus Wi-Fi or VPN): Portainer is not reachable from outside.
#
# Environment (from Doppler project uk-transcribe, config prd; never printed):
#   PORTAINER_URL          https://194.171.191.226:3085 (no trailing slash)
#   PORTAINER_TOKEN        Portainer access token (My account -> Access tokens)
#   PORTAINER_ENDPOINT_ID  16, "Student apps (cluster)"
#   PORTAINER_INSECURE     1: Portainer's certificate is self-signed
# Optional:
#   WEB_PORT      default 30500 (the cluster allows 30000-32699)
#   NODE_IP       default 194.171.191.229, used for the URL it prints
#   WAIT_SECONDS  default 1800 (the first start pulls a multi-GB image and the model)
set -euo pipefail

cmd="${1:-}"
shift || true

STACK_NAME="ukt"
STACK_FILE="$(dirname "$0")/cluster.yml"
OWNER="alex-krasnoshtanov"
API_REPO="${OWNER}/uk-transcribe"
WEB_REPO="${OWNER}/uk-transcribe-web"
SERVICES="ukt-api ukt-web"
# name:size:uid:gid; the api runs as 10001 (Dockerfile, deploy/cluster.yml)
VOLUMES="ukt-data:20Gi:10001:10001 ukt-models:10Gi:10001:10001"
WEB_PORT="${WEB_PORT:-30500}"
NODE_IP="${NODE_IP:-194.171.191.229}"
WAIT_SECONDS="${WAIT_SECONDS:-1800}"
POLL_SECONDS=10

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
[[ -z "$cmd" || "$cmd" == "-h" || "$cmd" == "--help" ]] && usage

for tool in curl jq; do
  command -v "$tool" >/dev/null || { echo "error: $tool is not installed" >&2; exit 1; }
done
: "${PORTAINER_URL:?run through doppler: doppler run -- bash deploy/ukt.sh ...}"
: "${PORTAINER_TOKEN:?missing in Doppler}" "${PORTAINER_ENDPOINT_ID:?missing in Doppler}"
PORTAINER_URL="${PORTAINER_URL%/}"

# jq on Windows ends lines with CRLF.
jq() { command jq "$@" | tr -d '\r'; }
curl_opts=(--silent --show-error -H "X-API-Key: ${PORTAINER_TOKEN}")
[[ "${PORTAINER_INSECURE:-0}" == "1" ]] && curl_opts+=(--insecure)
api() { curl "${curl_opts[@]}" --max-time 600 --fail-with-body "$@"; }
status_code() { curl "${curl_opts[@]}" --max-time 60 --output /dev/null --write-out '%{http_code}' "$@"; }
DOCKER="${PORTAINER_URL}/api/endpoints/${PORTAINER_ENDPOINT_ID}/docker"
uri() { jq -rn --arg v "$1" '$v | @uri'; }
logs_of() { api "${DOCKER}/containers/$1/logs?stdout=1&stderr=1&tail=${2:-80}" | LC_ALL=C tr -d '\000-\010\016-\037' || true; }

check_reachable() {
  local code
  code=$(status_code "${PORTAINER_URL}/api/system/status" || true)
  if [[ "$code" == "000" ]]; then
    echo "error: Portainer is unreachable; are you on campus Wi-Fi or the VPN?" >&2
    exit 1
  fi
  code=$(status_code "${PORTAINER_URL}/api/stacks" || true)
  if [[ "$code" == "401" || "$code" == "403" ]]; then
    echo "error: Portainer refused the token (HTTP $code); create a new one and update Doppler" >&2
    exit 1
  fi
}

stack_info() {  # "<id> <type> <status>" or empty
  api "${PORTAINER_URL}/api/stacks" |
    jq -r --arg n "$STACK_NAME" --argjson e "$PORTAINER_ENDPOINT_ID" \
      'map(select(.Name == $n and .EndpointId == $e)) | first | if . then "\(.Id) \(.Type) \(.Status)" else "" end'
}

container_of() {  # container id of a service, or empty
  local filters
  filters=$(jq -cn --arg p "$STACK_NAME" --arg s "$1" \
    '{label: ["com.docker.compose.project=\($p)", "com.docker.compose.service=\($s)"]}')
  api "${DOCKER}/containers/json?all=1&filters=$(uri "$filters")" | jq -r '.[0].Id // empty' || true
}

# A public GHCR image can be pulled anonymously; d2k needs exactly that.
image_exists() {  # repo tag
  local token
  token=$(curl -s --max-time 30 "https://ghcr.io/token?scope=repository:$1:pull" | jq -r '.token // empty')
  [[ -n "$token" ]] || return 1
  [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -I \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json" \
    "https://ghcr.io/v2/$1/manifests/$2")" == "200" ]]
}

port_taken_by_others() {  # prints the container holding WEB_PORT outside our stack
  api "${DOCKER}/containers/json?all=1" | jq -r --argjson p "$WEB_PORT" --arg s "$STACK_NAME" '
    .[] | select((.Labels["com.docker.compose.project"] // "") != $s)
        | select(any(.Ports[]?; .PublicPort == $p)) | .Names[0]' | head -1
}

ensure_volumes() {
  local spec vol size uid gid helper ok
  for spec in $VOLUMES; do
    IFS=: read -r vol size uid gid <<<"$spec"
    [[ "$(status_code "${DOCKER}/volumes/${vol}")" != "404" ]] && continue
    echo "creating volume ${vol} (${size})"
    jq -n --arg n "$vol" --arg s "$size" '{Name: $n, DriverOpts: {size: $s}}' |
      api -X POST -H "Content-Type: application/json" --data @- "${DOCKER}/volumes/create" >/dev/null
    # New volumes are root-owned; a root helper hands it to the app's user once
    # (restart unless-stopped: the cluster allows no run-once pods).
    helper="${vol}-chown"
    jq -n --arg v "$vol" --arg owner "${uid}:${gid}" '{
        Image: "busybox:1.37", User: "0:0",
        Cmd: ["sh", "-c", "chown \($owner) /data && chmod 0755 /data && echo CHOWN_OK; sleep 3600"],
        HostConfig: {Mounts: [{Type: "volume", Source: $v, Target: "/data"}],
                     RestartPolicy: {Name: "unless-stopped"}}}' |
      api -X POST -H "Content-Type: application/json" --data @- \
        "${DOCKER}/containers/create?name=${helper}" >/dev/null
    api -X POST "${DOCKER}/containers/${helper}/start" >/dev/null
    ok=""
    for _ in $(seq 1 60); do
      if logs_of "$helper" 20 | grep -q CHOWN_OK; then ok=1; break; fi
      sleep 5
    done
    api -X DELETE "${DOCKER}/containers/${helper}?force=1" >/dev/null || true
    [[ -n "$ok" ]] || { echo "error: could not hand volume ${vol} to ${uid}:${gid}" >&2; exit 1; }
    echo "volume ${vol} handed to ${uid}:${gid}"
  done
}

service_state() {  # "<ok|wait|fail> <cid> <detail>"
  local cid info image state health restarts
  cid=$(container_of "$1")
  [[ -z "$cid" ]] && { echo "wait - no-container-yet"; return; }
  info=$(api "${DOCKER}/containers/${cid}/json") || { echo "wait ${cid} not-readable"; return; }
  read -r image state health restarts < <(jq -r \
    '"\(.Config.Image) \(.State.Status) \(.State.Health.Status // "none") \(.RestartCount // 0)"' <<<"$info")
  if [[ -n "${2:-}" && "$image" != *":$2" ]]; then
    echo "wait ${cid} still-on-${image##*:}"
  elif [[ "$health" == "unhealthy" || "$state" == "dead" ]]; then
    echo "fail ${cid} ${state}/${health}"
  elif ((restarts >= 3)); then
    echo "fail ${cid} restarted-${restarts}-times"
  elif [[ "$state" == "running" && "$health" == "healthy" ]]; then
    echo "ok ${cid} healthy"
  else
    echo "wait ${cid} ${state}/${health}"
  fi
}

wait_healthy() {  # [tag]
  local deadline=$((SECONDS + WAIT_SECONDS)) last="" summary all_ok failed verdict cid detail service
  echo "waiting up to ${WAIT_SECONDS}s for ${SERVICES// /, } to be healthy (the first start downloads the model)"
  while :; do
    summary="" all_ok=1 failed=""
    for service in $SERVICES; do
      read -r verdict cid detail < <(service_state "$service" "${1:-}")
      summary+="${service}: ${verdict} ${detail}; "
      [[ "$verdict" != "ok" ]] && all_ok=""
      [[ "$verdict" == "fail" ]] && failed+="${service}=${cid} "
    done
    [[ "$summary" != "$last" ]] && { echo "$summary"; last="$summary"; }
    if [[ -n "$all_ok" ]]; then return 0; fi
    if [[ -n "$failed" ]] || ((SECONDS >= deadline)); then
      [[ -z "$failed" ]] && echo "error: not healthy after ${WAIT_SECONDS}s" >&2
      for pair in $failed; do
        echo "--- log of ${pair%%=*}"
        logs_of "${pair#*=}"
      done
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
}

healthz() {
  local cid
  cid=$(container_of ukt-web)
  [[ -z "$cid" ]] && return 0
  # The dashboard proxies the API's /healthz at /api/backend/healthz.
  curl -s --max-time 10 "http://${NODE_IP}:${WEB_PORT}/api/backend/healthz" | jq -c . 2>/dev/null || true
}

cmd_deploy() {
  local tag="${1:-}"
  if [[ -z "$tag" ]]; then
    git -C "$(dirname "$0")" fetch -q origin main 2>/dev/null || true
    tag="sha-$(git -C "$(dirname "$0")" rev-parse --short=7 origin/main)"
  fi
  echo "deploying ${tag}"
  for repo in "$API_REPO" "$WEB_REPO"; do
    if ! image_exists "$repo" "$tag"; then
      echo "error: ghcr.io/${repo}:${tag} is not pullable anonymously." >&2
      echo "       Wait for the images workflow to finish, and make the package Public once" >&2
      echo "       (github.com/${OWNER}?tab=packages -> Package settings -> Change visibility)." >&2
      exit 1
    fi
  done
  check_reachable
  local holder
  holder=$(port_taken_by_others)
  if [[ -n "$holder" ]]; then
    echo "error: port ${WEB_PORT} is used by ${holder}; set WEB_PORT to another free port in 30000-32699" >&2
    exit 1
  fi
  ensure_volumes

  local env_json stack_id stack_type _status
  env_json=$(jq -n --arg a "ghcr.io/${API_REPO}:${tag}" --arg w "ghcr.io/${WEB_REPO}:${tag}" --arg p "$WEB_PORT" \
    '[{name: "API_IMAGE", value: $a}, {name: "WEB_IMAGE", value: $w}, {name: "WEB_PORT", value: $p}]')
  read -r stack_id stack_type _status < <(stack_info) || true
  if [[ -n "${stack_id:-}" && "$stack_type" != "2" ]]; then
    echo "error: stack ${STACK_NAME} (id ${stack_id}) is a Swarm stack (no GPU there); remove it in Portainer" >&2
    exit 1
  fi
  if [[ -n "${stack_id:-}" ]]; then
    echo "updating stack ${STACK_NAME} (id ${stack_id})"
    jq -n --rawfile f "$STACK_FILE" --argjson env "$env_json" \
      '{stackFileContent: $f, env: $env, prune: true, pullImage: false}' |
      api -X PUT -H "Content-Type: application/json" --data @- \
        "${PORTAINER_URL}/api/stacks/${stack_id}?endpointId=${PORTAINER_ENDPOINT_ID}" >/dev/null
  else
    echo "creating stack ${STACK_NAME}"
    stack_id=$(jq -n --arg n "$STACK_NAME" --rawfile f "$STACK_FILE" --argjson env "$env_json" \
      '{name: $n, stackFileContent: $f, env: $env}' |
      api -X POST -H "Content-Type: application/json" --data @- \
        "${PORTAINER_URL}/api/stacks/create/standalone/string?endpointId=${PORTAINER_ENDPOINT_ID}" |
      jq -r .Id)
  fi

  # Portainer deploys in the background: stack status 3 while busy, 1 when done.
  local deadline=$((SECONDS + 600)) stack
  while :; do
    stack=$(api "${PORTAINER_URL}/api/stacks/${stack_id}")
    case "$(jq -r .Status <<<"$stack")" in
      1) break ;;
      3) ;;
      *) echo "error: Portainer could not deploy: $(jq -r '.DeploymentStatus[-1].Message // "no message"' <<<"$stack")" >&2
         exit 1 ;;
    esac
    ((SECONDS < deadline)) || { echo "error: Portainer still deploying after 600s" >&2; exit 1; }
    sleep "$POLL_SECONDS"
  done
  wait_healthy "$tag"
  echo "deployed ${tag}: http://${NODE_IP}:${WEB_PORT}"
  healthz
}

cmd_stop() {
  check_reachable
  local stack_id _t st service cid
  read -r stack_id _t st < <(stack_info) || true
  [[ -n "${stack_id:-}" ]] || { echo "no stack ${STACK_NAME}; nothing to stop"; return 0; }
  if [[ "$st" == "2" ]]; then echo "already stopped"; return 0; fi
  if api -X POST "${PORTAINER_URL}/api/stacks/${stack_id}/stop?endpointId=${PORTAINER_ENDPOINT_ID}" >/dev/null 2>&1; then
    echo "stack ${STACK_NAME} stopped: the GPU slice is released, the volumes keep all data"
    return 0
  fi
  # Fallback: stop the containers one by one.
  echo "stack stop was refused; stopping the containers instead"
  for service in $SERVICES; do
    cid=$(container_of "$service")
    [[ -n "$cid" ]] && api -X POST "${DOCKER}/containers/${cid}/stop?t=30" >/dev/null && echo "stopped ${service}"
  done
}

cmd_start() {
  check_reachable
  local stack_id _t st service cid
  read -r stack_id _t st < <(stack_info) || true
  [[ -n "${stack_id:-}" ]] || { echo "error: no stack ${STACK_NAME}; run: deploy" >&2; exit 1; }
  if [[ "$st" == "2" ]]; then
    api -X POST "${PORTAINER_URL}/api/stacks/${stack_id}/start?endpointId=${PORTAINER_ENDPOINT_ID}" >/dev/null
  else
    for service in $SERVICES; do
      cid=$(container_of "$service")
      [[ -n "$cid" ]] && api -X POST "${DOCKER}/containers/${cid}/start" >/dev/null 2>&1 || true
    done
  fi
  wait_healthy
  echo "running: http://${NODE_IP}:${WEB_PORT}"
  healthz
}

cmd_status() {
  check_reachable
  local stack_id stack_type st service verdict cid detail
  read -r stack_id stack_type st < <(stack_info) || true
  if [[ -z "${stack_id:-}" ]]; then echo "stack ${STACK_NAME}: not deployed"; return 0; fi
  echo "stack ${STACK_NAME}: id ${stack_id}, $([[ "$st" == "1" ]] && echo active || echo stopped)"
  for service in $SERVICES; do
    read -r verdict cid detail < <(service_state "$service")
    echo "  ${service}: ${detail}"
  done
  echo "  url: http://${NODE_IP}:${WEB_PORT}"
  healthz
}

cmd_logs() {
  check_reachable
  local service="${1:-ukt-api}" cid
  cid=$(container_of "$service")
  [[ -n "$cid" ]] || { echo "error: no container for ${service}" >&2; exit 1; }
  logs_of "$cid" "${2:-200}"
}

case "$cmd" in
  deploy) cmd_deploy "$@" ;;
  stop) cmd_stop ;;
  start) cmd_start ;;
  status) cmd_status ;;
  logs) cmd_logs "$@" ;;
  *) usage 1 ;;
esac
