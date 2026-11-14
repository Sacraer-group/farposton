#!/bin/bash
set -euo pipefail

revision=${SSH_ORIGINAL_COMMAND:-}
[[ $revision =~ ^[0-9a-f]{40}$ ]] || exit 1
base=/home/tech/flats
exec 9>"$base/deploy.lock"
flock 9
release="$base/releases/$revision"
mkdir -p "$release"
tar -xf - -C "$release"
sudo docker build -f "$base/Dockerfile" -t "farposton-flats:$revision" "$release"

previous=$(sed -n 's/^    image: farposton-flats://p' "$base/compose.yaml")
[[ $previous =~ ^[0-9a-f]{40}$ ]] || exit 1
sed -i "s/^    image: farposton-flats:.*/    image: farposton-flats:$revision/" "$base/compose.yaml"
rollback() {
  sed -i "s/^    image: farposton-flats:.*/    image: farposton-flats:$previous/" "$base/compose.yaml"
  sudo docker compose -p flats-preview -f "$base/compose.yaml" up -d
}
trap rollback ERR
sudo docker compose -p flats-preview -f "$base/compose.yaml" up -d
for attempt in {1..30}; do
  if [[ $(sudo docker inspect --format '{{.State.Health.Status}}' flats-preview-flats-1) == healthy ]]; then
    printf '%s\n' "$revision" | sudo tee "$base/.deployed-commit" >/dev/null
    trap - ERR
    exit 0
  fi
  sleep 2
done
false
