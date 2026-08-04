#!/usr/bin/env bash
set -euo pipefail

readonly ARM64_IMAGE="zotero/translation-server@sha256:a80abfaaab0d84c8cc4b0ef79e4fde94b391420ee3a1e69d680fc89a18bff115"
readonly AMD64_IMAGE="ghcr.io/hoanganhduc/translation-server@sha256:6bb209778e0403d81285404fc9ca5bd142f91e090d14a5541ac33018531c1329"
readonly REQUIRED_COMPOSE_IMAGE='${ZOTERO_TS_IMAGE:?ZOTERO_TS_IMAGE must be set}'

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

is_digest_only_image() {
    local ref="$1"
    local repository="${ref%@sha256:*}"
    local digest="${ref##*@sha256:}"
    local leaf="${repository##*/}"

    [[ "$ref" == *@sha256:* ]] || return 1
    [[ "$repository" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]*$ ]] || return 1
    [[ "$leaf" != *:* ]] || return 1
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
}

bounded_integer() {
    local name="$1"
    local value="$2"
    local minimum="$3"
    local maximum="$4"

    [[ "$value" =~ ^[0-9]+$ ]] || fail "$name must be an integer from $minimum to $maximum"
    (( value >= minimum && value <= maximum )) || \
        fail "$name must be an integer from $minimum to $maximum"
}

[[ "$(uname -s)" == "Linux" ]] || fail "Translation Server container startup is supported only on Linux"

case "$(uname -m)" in
    aarch64|arm64)
        default_image="$ARM64_IMAGE"
        ;;
    x86_64|amd64)
        default_image="$AMD64_IMAGE"
        ;;
    *)
        fail "unsupported Linux architecture: $(uname -m)"
        ;;
esac

ZOTERO_TS_IMAGE="${ZOTERO_TS_IMAGE:-$default_image}"
is_digest_only_image "$ZOTERO_TS_IMAGE" || \
    fail "ZOTERO_TS_IMAGE must be an image reference pinned only by @sha256:<64 lowercase hex characters>"
export ZOTERO_TS_IMAGE

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
readonly SCRIPT_DIR
readonly COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

[[ -e "$COMPOSE_FILE" ]] || fail "required Compose file is missing: $COMPOSE_FILE"
[[ -f "$COMPOSE_FILE" && ! -L "$COMPOSE_FILE" ]] || \
    fail "Compose file must be a regular, non-symlink file: $COMPOSE_FILE"

mapfile -t compose_images < <(
    awk '
        /^[[:space:]]*image[[:space:]]*:/ {
            line = $0
            sub(/^[[:space:]]*/, "", line)
            sub(/[[:space:]]+$/, "", line)
            print line
        }
    ' "$COMPOSE_FILE"
)
[[ "${#compose_images[@]}" -eq 1 ]] || \
    fail "Compose file must contain exactly one image declaration"
[[ "${compose_images[0]}" == "image: $REQUIRED_COMPOSE_IMAGE" ]] || \
    fail "Compose image must be exactly: image: $REQUIRED_COMPOSE_IMAGE"
if grep -Eq '^[[:space:]]*build[[:space:]]*:' "$COMPOSE_FILE"; then
    fail "Compose file must not contain a build declaration"
fi

command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"

cd "$SCRIPT_DIR"
mapfile -t resolved_images < <(
    docker compose -f "$COMPOSE_FILE" config --images | awk 'NF { print }'
)
[[ "${#resolved_images[@]}" -eq 1 && "${resolved_images[0]}" == "$ZOTERO_TS_IMAGE" ]] || \
    fail "Compose resolved an image other than the selected digest-pinned reference"

readonly HEALTH_URL="${ZOTERO_TS_HEALTH_URL:-http://localhost:1969/}"
readonly HEALTH_ATTEMPTS="${ZOTERO_TS_HEALTH_ATTEMPTS:-30}"
readonly HEALTH_INTERVAL_SECONDS="${ZOTERO_TS_HEALTH_INTERVAL_SECONDS:-2}"
readonly HEALTH_REQUEST_TIMEOUT_SECONDS="${ZOTERO_TS_HEALTH_REQUEST_TIMEOUT_SECONDS:-5}"
bounded_integer "ZOTERO_TS_HEALTH_ATTEMPTS" "$HEALTH_ATTEMPTS" 1 60
bounded_integer "ZOTERO_TS_HEALTH_INTERVAL_SECONDS" "$HEALTH_INTERVAL_SECONDS" 0 10
bounded_integer "ZOTERO_TS_HEALTH_REQUEST_TIMEOUT_SECONDS" "$HEALTH_REQUEST_TIMEOUT_SECONDS" 1 10

docker compose -f "$COMPOSE_FILE" up -d
echo "Translation Server starting with $ZOTERO_TS_IMAGE"
echo "Waiting for server readiness at $HEALTH_URL"

attempt=1
while (( attempt <= HEALTH_ATTEMPTS )); do
    http_code="$(
        curl --silent --show-error --output /dev/null \
            --write-out '%{http_code}' \
            --max-time "$HEALTH_REQUEST_TIMEOUT_SECONDS" \
            "$HEALTH_URL" 2>/dev/null || true
    )"
    if [[ "$http_code" == "200" || "$http_code" == "404" ]]; then
        echo "Translation Server is ready."
        exit 0
    fi
    if (( attempt < HEALTH_ATTEMPTS )); then
        sleep "$HEALTH_INTERVAL_SECONDS"
    fi
    attempt=$((attempt + 1))
done

fail "Translation Server did not become ready after $HEALTH_ATTEMPTS attempts"
