"""Exercise real extra-data sandboxes and the image's native Flatpak seed."""

import base64
import configparser
import hashlib
import http.server
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('extra_data_flatpaks', ROOT / 'scripts/flatpaks.py')
flatpaks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flatpaks)


@unittest.skipUnless(platform.machine() == 'x86_64' and
                     all(shutil.which(tool) for tool in ('flatpak', 'ostree', 'gpg', 'cc')),
                     'Native extra-data check needs x86_64, Flatpak, OSTree, GPG and a C compiler.')
class NativeExtraDataTests(unittest.TestCase):
    def test_extra_data_applies_and_remains_an_online_native_default(self):
        with tempfile.TemporaryDirectory(prefix='avethyos-extra-data-test-') as temporary:
            work = Path(temporary)
            env = dict(os.environ, HOME=str(work / 'home'), XDG_DATA_HOME=str(work / 'data'),
                       XDG_CACHE_HOME=str(work / 'cache'), XDG_RUNTIME_DIR=str(work / 'runtime'),
                       XDG_CONFIG_HOME=str(work / 'config-home'), FLATPAK_USER_DIR=str(work / 'installation'),
                       FLATPAK_SYSTEM_DIR=str(work / 'unused-system'), FLATPAK_CONFIG_DIR=str(work / 'config'),
                       FLATPAK_TRIGGERSDIR=str(work / 'triggers'), GNUPGHOME=str(work / 'gpg'))
            for key in ('HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR',
                        'XDG_CONFIG_HOME', 'FLATPAK_CONFIG_DIR', 'FLATPAK_TRIGGERSDIR', 'GNUPGHOME'):
                Path(env[key]).mkdir(mode=0o700)

            def run(*command, environment=env):
                result = subprocess.run(command, env=environment, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            help_result = subprocess.run(['flatpak', 'preinstall', '--help'], env=env,
                                         text=True, capture_output=True)
            if help_result.returncode:
                self.skipTest('Installed Flatpak lacks native preinstall support.')

            # A tiny static executable needs no downloaded runtime or libc. It
            # proves apply_extra actually ran in Flatpak's native sandbox.
            source = work / 'apply-extra.c'
            source.write_text('''
static long call(long n,long a,long b,long c,long d) {
  long result; register long r10 __asm__("r10")=d;
  __asm__ volatile("syscall" : "=a"(result) : "a"(n),"D"(a),"S"(b),"d"(c),"r"(r10)
                   : "rcx","r11","memory");
  return result;
}
void _start(void) {
  char data[128];
  long src=call(257,-100,(long)"/app/extra/payload",0,0);
  long count=src<0 ? -1 : call(0,src,(long)data,128,0);
  long dest=call(257,-100,(long)"/app/extra/applied",577,0644);
  long wrote=dest<0 || count<0 ? -1 : call(1,dest,(long)data,count,0);
  call(60,wrote==count && count>0 ? 0:1,0,0,0);
  __builtin_unreachable();
}
''')
            executable = work / 'apply_extra'
            run('cc', '-nostdlib', '-static', '-Os', '-s', '-o', str(executable), str(source))
            run('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                '--quick-generate-key', 'Extra-data fixture <test@example.invalid>', 'ed25519', 'sign', '0')
            key = next(line.split(':')[9] for line in run('gpg', '--with-colons', '--list-secret-keys').splitlines()
                       if line.startswith('fpr:'))
            public_key = subprocess.run(['gpg', '--export', key], env=env,
                                        capture_output=True, check=True).stdout
            arch = run('flatpak', '--default-arch').strip()
            runtime = 'org.avethyos.ExtraPlatform'
            ordinary = 'org.avethyos.PlainFixture'
            extra = 'org.avethyos.ExtraFixture'
            extension = ordinary + '.Extra'
            repository = work / 'repository'
            content = b'Native extra-data sandbox passed.\n'

            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)

                def log_message(self, *args):
                    pass

            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f'http://127.0.0.1:{server.server_port}/payload'
            for name, is_runtime in ((runtime, True), (extension, True), (extra, False), (ordinary, False)):
                build = work / name
                (build / 'files/bin').mkdir(parents=True)
                kind = 'Runtime' if is_runtime else 'Application'
                metadata = (f'[{kind}]\nname={name}\nruntime={runtime}/{arch}/stable\n'
                            f'sdk={runtime}/{arch}/stable\n')
                if name == ordinary:
                    metadata += f'\n[Extension {extension}]\ndirectory=lib/extra\nversion=stable\n'
                if name == extension:
                    metadata += f'\n[ExtensionOf]\nref=app/{ordinary}/{arch}/stable\n'
                (build / 'metadata').write_text(metadata)
                if name in (extra, extension):
                    shutil.copyfile(executable, build / 'files/bin/apply_extra')
                    (build / 'files/bin/apply_extra').chmod(0o755)
                    run('flatpak', 'build-finish', '--no-exports',
                        f'--extra-data=payload:{hashlib.sha256(content).hexdigest()}:{len(content)}:{len(content)}:{url}',
                        '--metadata=Extra Data=NoRuntime=true', str(build))
                elif not is_runtime:
                    run('flatpak', 'build-finish', '--no-exports', str(build))
                options = ['--runtime'] if is_runtime else []
                run('flatpak', 'build-export', '--disable-sandbox', '--files=files',
                    f'--collection-id={flatpaks.COLLECTION}', f'--gpg-sign={key}',
                    f'--gpg-homedir={env["GNUPGHOME"]}', *options, str(repository), str(build), 'stable')

            image = work / 'image'
            remote = image / 'usr/share/flatpak/remotes.d/flathub.flatpakrepo'
            remote.parent.mkdir(parents=True)
            remote.write_text(f'[Flatpak Repo]\nTitle=Fixture\nUrl={repository.as_uri()}\n'
                              f'CollectionID={flatpaks.COLLECTION}\n'
                              f'GPGKey={base64.b64encode(public_key).decode()}\n')
            (work / flatpaks.LISTS[0]).write_text(f'{ordinary}\n{extra}\n')
            (work / flatpaks.LISTS[1]).write_text('')

            def isolated_runner(command, **kwargs):
                # Run the real helper's commands in a dedicated user
                # installation, leaving this computer's installations alone.
                if command[:2] == ['flatpak', f'--installation={flatpaks.INSTALLATION}']:
                    command = ['flatpak', '--user', *command[2:]]
                kwargs['env'] = dict(kwargs['env'], **env)
                kwargs.setdefault('text', True)
                kwargs.setdefault('stdout', subprocess.PIPE)
                kwargs.setdefault('stderr', subprocess.PIPE)
                return subprocess.run(command, **kwargs)

            flatpaks.build(work, root=image, runner=isolated_runner, pause=lambda delay: None)
            for app in (extra, extension):
                location = Path(run('flatpak', 'info', '--user', '--show-location', app).strip())
                self.assertEqual((location / 'files/extra/applied').read_bytes(), content)
            seed = image / flatpaks.SEED / '.ostree/repo'
            exported = run('ostree', f'--repo={seed}', 'refs', '--collections')
            self.assertIn(f'app/{ordinary}/{arch}/stable', exported)
            self.assertIn(f'runtime/{runtime}/{arch}/stable', exported)
            self.assertNotIn(f'app/{extra}/{arch}/stable', exported)
            self.assertNotIn(f'runtime/{extension}/{arch}/stable', exported)
            defaults = configparser.ConfigParser()
            defaults.read(image / flatpaks.PREINSTALL)
            for app in (ordinary, extra):
                self.assertTrue(defaults[f'Flatpak Preinstall {app}'].getboolean('Install'))

            # On a fresh boot, the native service installs both defaults;
            # ordinary refs use the seed and extra-data is fetched online.
            boot_env = dict(env, FLATPAK_USER_DIR=str(work / 'boot-installation'),
                            FLATPAK_DATA_DIR=str(image / 'usr/share/flatpak'))
            run('flatpak', 'remote-add', '--user', '--from', 'flathub', str(remote), environment=boot_env)
            run('flatpak', 'preinstall', '--user', '-y', '--noninteractive',
                f'--sideload-repo={seed}', environment=boot_env)
            installed = set(run('flatpak', 'list', '--user', '--app', '--columns=application',
                                environment=boot_env).splitlines())
            self.assertEqual(installed, {ordinary, extra})
            location = Path(run('flatpak', 'info', '--user', '--show-location', extra,
                                environment=boot_env).strip())
            self.assertEqual((location / 'files/extra/applied').read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
