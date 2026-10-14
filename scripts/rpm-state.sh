#!/bin/bash
set -euo pipefail

# Apply package state declarations only inside an unbooted image build.
if [[ -e /run/ostree-booted || ! -e /run/.containerenv ]]; then
    printf 'RPM state setup requires an unbooted build container.\n' >&2
    exit 1
fi

shopt -s nullglob
for rule in /custom/tmpfiles.d/*.conf; do
    package=${rule##*/}
    package=${package%.conf}
    target=/usr/lib/tmpfiles.d/avethyos-personal-$package.conf
    # Explicit rules supplied through files/ or build.sh take precedence.
    if rpm -q -- "$package" >/dev/null 2>&1 && [[ ! -e $target && ! -L $target ]]; then
        install -Dm0644 "$rule" "$target"
    fi
done
