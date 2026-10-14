#!/bin/bash
set -euo pipefail

rpm -q blueman >/dev/null
rule=/usr/lib/tmpfiles.d/avethyos-personal-blueman.conf
grep -Fxq 'd /var/lib/blueman 0755 root root - -' "$rule"

# Keep a user's explicit rule when setup is run again after their build hook.
printf '# Keep this custom rule\n' >> "$rule"
expected=$(sha256sum "$rule")
bash /custom/rpm-state.sh
[[ $(sha256sum "$rule") == "$expected" ]]

state_root=$(mktemp -d)
trap 'rm -rf -- "$state_root"' EXIT
mkdir -p "$state_root/etc" "$state_root/usr/lib/tmpfiles.d"
printf 'root:x:0:0::/:/bin/sh\n' > "$state_root/etc/passwd"
printf 'root:x:0:\n' > "$state_root/etc/group"
cp "$rule" "$state_root/usr/lib/tmpfiles.d/"
systemd-tmpfiles --root="$state_root" --create "${rule##*/}"
directory=$state_root/var/lib/blueman
[[ $(stat -c '%a:%u:%g' "$directory") == 755:0:0 ]]

# A later boot repairs directory permissions and preserves existing data.
printf 'existing network state\n' > "$directory/network.state"
chmod 0700 "$directory"
systemd-tmpfiles --root="$state_root" --create "${rule##*/}"
[[ $(stat -c '%a:%u:%g' "$directory") == 755:0:0 ]]
[[ $(cat "$directory/network.state") == 'existing network state' ]]
