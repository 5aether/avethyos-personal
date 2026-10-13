#!/bin/bash
set -euo pipefail
BIB_IMAGE="${BIB_IMAGE:-quay.io/centos-bootc/bootc-image-builder:latest}"
image="${1:?Specify an image}:${2:?Specify a tag}"
output="${3:-output}"
source_image="${ISO_SOURCE_REF:-$image}"
config="${ISO_CONFIG_FILE:-${PWD}/disk_config/iso.toml}"
config=$(realpath "$config")
root=()
if (( UID != 0 )); then
    root=(sudo)
fi

# The image builder uses root's container storage. Copy a local user build
# through Podman's native archive stream only when the two copies differ.
if podman image exists "$source_image"; then
    local_id=$(podman image inspect --format '{{.Id}}' "$source_image")
    root_id=$("${root[@]}" podman image inspect --format '{{.Id}}' "$source_image" 2>/dev/null || true)
    if [[ "$local_id" != "$root_id" ]]; then
        podman save --format oci-archive "$source_image" | "${root[@]}" podman load
    fi
else
    authentication=()
    if [[ -n "${REGISTRY_AUTH_FILE:-}" ]]; then
        authentication=(--authfile "$REGISTRY_AUTH_FILE")
    fi
    "${root[@]}" podman pull --retry=3 "${authentication[@]}" "$source_image"
fi

# CI pulls the released digest, then gives that local image its update tag.
# Image Builder reads local root storage: the ISO contains the exact release
# while the installed system keeps following latest/nvidia for future updates.
if [[ "$source_image" != "$image" ]]; then
    "${root[@]}" podman tag "$source_image" "$image"
fi

temporary=$(mktemp -d "${PWD}/_build-iso.XXXXXXXXXX")
trap '"${root[@]}" rm -rf -- "$temporary"' EXIT
"${root[@]}" podman run --rm --privileged --pull=newer --net=host \
    --security-opt label=type:unconfined_t \
    -v "$config:/config.toml:ro" \
    -v "$temporary:/output" \
    -v /var/lib/containers/storage:/var/lib/containers/storage \
    "$BIB_IMAGE" build --type anaconda-iso --output /output \
    --use-librepo=True --rootfs=btrfs "$image"
mkdir -p "$output"
"${root[@]}" cp -a "$temporary/." "$output/"
"${root[@]}" chown -R "$(id -u):$(id -g)" "$output"
printf 'Installer: %s/bootiso/install.iso\n' "$output"
