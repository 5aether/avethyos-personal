#!/bin/bash
set -euo pipefail

read_packages() {
    local line
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=${line%%#*}
        # Trim spaces without evaluating package names as shell commands.
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [[ -n "$line" ]] || continue
        if [[ ! "$line" =~ ^[a-zA-Z0-9][a-zA-Z0-9._+:-]*$ ]]; then
            printf 'Invalid package name in %s: %s\n' "$1" "$line" >&2
            return 1
        fi
        printf '%s\n' "$line"
    done < "$1"
}

# Command substitution preserves the validation status; mapfile alone would not.
added=$(read_packages /custom/packages.txt)
removed=$(read_packages /custom/remove-packages.txt)
packages=()
remove=()
[[ -z "$added" ]] || mapfile -t packages <<< "$added"
[[ -z "$removed" ]] || mapfile -t remove <<< "$removed"
if (( ${#remove[@]} )); then
    dnf5 remove -y --no-autoremove "${remove[@]}"
fi
if (( ${#packages[@]} )); then
    dnf5 install -y --setopt=install_weak_deps=False "${packages[@]}"
fi

# Flatpak's native preinstall service applies these choices on new installs and
# after OS updates, while preserving apps that users install or uninstall.
python3 -B /custom/flatpaks.py build /custom

# files/usr/share/example becomes /usr/share/example in the finished image.
tar -C /files --exclude=.gitkeep -cf - . | tar -C / -xpf -
install -Dm644 /custom/kernel-arguments.toml /usr/lib/bootc/kargs.d/90-personal.toml
bash /custom/build.sh
# Add native startup rules for installed RPMs that omit them upstream.
bash /custom/rpm-state.sh
# Use the same build-state cleanup as AvethyOS; never run this on an installed OS.
bash /usr/libexec/avethyos-image-cleanup
bootc container lint --fatal-warnings
