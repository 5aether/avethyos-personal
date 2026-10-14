#!/usr/bin/python3
"""Build Flatpak defaults and extend AvethyOS's offline app repository."""

import argparse
import configparser
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


LISTS = ('flatpak-packages.txt', 'remove-flatpak-packages.txt')
COLLECTION = 'org.flathub.Stable'
INSTALLATION = 'avethyos-personal-build'
PREINSTALL = Path('usr/share/flatpak/preinstall.d/zz-avethyos-personal.preinstall')
SEED = Path('usr/share/avethyos/flatpak-seed')
APP_ID = re.compile(r'(?:[A-Za-z_][A-Za-z0-9_]*\.){2,}[A-Za-z_][A-Za-z0-9_-]*')


def read_lists(directory):
    result = []
    for filename in LISTS:
        apps = []
        for number, line in enumerate((directory / filename).read_text().splitlines(), 1):
            app = line.split('#', 1)[0].strip()
            if not app:
                continue
            if len(app) > 255 or not APP_ID.fullmatch(app):
                raise ValueError(f'{filename}:{number}: put one Flatpak application ID on each line '
                                 '(for example org.mozilla.firefox).')
            if app not in apps:
                apps.append(app)
        result.append(apps)
    overlap = set(result[0]) & set(result[1])
    if overlap:
        raise ValueError('Do not add and remove the same Flatpak app: ' + ', '.join(sorted(overlap)))
    return result


def declaration(added, removed):
    content = '# Generated from flatpak-packages.txt and remove-flatpak-packages.txt.\n'
    for apps, install in ((added, 'true'), (removed, 'false')):
        for app in apps:
            content += (f'\n[Flatpak Preinstall {app}]\nBranch=stable\n'
                        f'CollectionID={COLLECTION}\nInstall={install}\n')
    return content


def write_defaults(added, removed, directory):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / PREINSTALL.name
    changed = {f'Flatpak Preinstall {app}' for app in added + removed}
    # Give each requested app one declaration. Keeping duplicate groups in
    # inherited files makes the result depend on Flatpak's file scan order.
    for path in directory.glob('*.preinstall'):
        if path == target:
            continue
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.optionxform = str
        config.read(path)
        if changed.intersection(config.sections()):
            for group in changed:
                config.remove_section(group)
            with path.open('w') as stream:
                config.write(stream, space_around_delimiters=False)
    target.write_text(declaration(added, removed))
    target.chmod(0o644)


def has_extra_data(metadata):
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    config.read_string(metadata)
    # Flatpak builds xa.extra-data-sources from the URI keys in this group.
    return (config.has_section('Extra Data') and
            any(key.startswith('uri') and value for key, value in config['Extra Data'].items()))


def build(directory, root=Path('/'), runner=subprocess.run, pause=time.sleep):
    added, removed = read_lists(directory)
    write_defaults(added, removed, (root / PREINSTALL).parent)
    if not added:
        return

    # Build into a temporary installation. /var/lib/flatpak is persistent on
    # installed computers and must never be the source of image defaults.
    config = root / f'etc/flatpak/installations.d/{INSTALLATION}.conf'
    if config.exists():
        raise ValueError(f'Temporary Flatpak installation already exists: {config}')
    config.parent.mkdir(parents=True, exist_ok=True)
    work_parent = root / 'var/tmp'
    work_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='avethyos-personal-flatpaks-', dir=work_parent) as temporary:
        work = Path(temporary)
        env = dict(os.environ, XDG_CACHE_HOME=str(work / 'cache'),
                   XDG_RUNTIME_DIR=str(work / 'runtime'), FLATPAK_TRIGGERSDIR=str(work / 'triggers'))
        for name in ('cache', 'runtime', 'triggers'):
            (work / name).mkdir(mode=0o700)
        config.write_text(f'[Installation "{INSTALLATION}"]\nPath={work / "installation"}\n'
                          'DisplayName=AvethyOS personal image build\n')
        config.chmod(0o644)
        try:
            # Match the nested sandbox used by Flatpak's apply_extra. Fail before
            # large downloads if the builder cannot support it. The workflow's
            # writable /proc/sys/user mount lets bwrap restrict its own user
            # namespace; neither host limits nor the app sandbox are changed.
            runner(['bwrap', '--unshare-user', '--unshare-pid', '--disable-userns',
                    '--ro-bind', '/', '/', '--dev', '/dev', '/bin/true'], env=env, check=True)
            prefix = ['flatpak', f'--installation={INSTALLATION}']
            runner([*prefix, 'remote-add', '--if-not-exists', '--from', 'flathub',
                    str(root / 'usr/share/flatpak/remotes.d/flathub.flatpakrepo')], env=env, check=True)
            runner([*prefix, 'remote-modify', f'--collection-id={COLLECTION}', 'flathub'], env=env, check=True)
            runner([*prefix, 'config', '--set', 'languages', '*'], env=env, check=True)
            refs = [f'{app}//stable' for app in added]
            seed = root / SEED
            cached = ([f'--sideload-repo={seed / ".ostree/repo"}']
                      if (seed / '.ostree/repo').is_dir() else [])
            for attempt in range(1, 6):
                try:
                    runner([*prefix, 'install', '--app', '-y', '--noninteractive', '--or-update',
                            *cached, 'flathub', *refs], env=env, check=True)
                    break
                except subprocess.CalledProcessError:
                    if attempt == 5:
                        raise
                    print(f'Flatpak installation failed; retrying ({attempt + 1}/5)...', file=sys.stderr)
                    pause(attempt * 10)
            offline = []
            for ref in refs:
                metadata = runner([*prefix, 'info', '--show-metadata', ref], env=env,
                                  check=True, text=True, stdout=subprocess.PIPE).stdout
                if has_extra_data(metadata):
                    print(f'{ref}: Flatpak requires an online first-boot installation '
                          '(extra data cannot be exported offline).', flush=True)
                else:
                    offline.append(ref)
            # Native create-usb retains inherited refs and includes dependencies,
            # excluding extra-data extensions itself. Direct extra-data apps are
            # unsupported by create-usb; their preinstall defaults stay enabled.
            if offline:
                seed.mkdir(parents=True, exist_ok=True)
                runner([*prefix, 'create-usb', '--app', str(seed), *offline], env=env, check=True)
        finally:
            config.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'build'))
    parser.add_argument('directory', type=Path, nargs='?', default=Path('.'))
    args = parser.parse_args()
    if args.command == 'build':
        if os.geteuid() != 0:
            parser.error('build runs as root inside the image build; use check for local validation.')
        build(args.directory)
    else:
        added, removed = read_lists(args.directory)
        print(f'Flatpak lists valid: {len(added)} added, {len(removed)} removed.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, configparser.Error, subprocess.CalledProcessError) as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
