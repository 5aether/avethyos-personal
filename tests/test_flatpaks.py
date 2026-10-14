"""Check image defaults without touching the computer's Flatpak installations."""

import configparser
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('personal_flatpaks', ROOT / 'scripts/flatpaks.py')
flatpaks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flatpaks)


class FlatpakListTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for filename in flatpaks.LISTS:
            (self.directory / filename).write_text('')

    def test_comments_empty_lists_case_and_duplicates(self):
        self.assertEqual(flatpaks.read_lists(self.directory), [[], []])
        (self.directory / flatpaks.LISTS[0]).write_text(
            '# default app\norg.mozilla.firefox\norg.videolan.VLC # video\n\norg.mozilla.firefox\n')
        self.assertEqual(flatpaks.read_lists(self.directory),
                         [['org.mozilla.firefox', 'org.videolan.VLC'], []])

    def test_only_application_ids_are_accepted(self):
        for app in ('firefox', '--assumeyes', 'org.mozilla.firefox//beta',
                    'org.mozilla.firefox org.videolan.VLC', '$(touch /tmp/no)',
                    '[Flatpak Preinstall org.test.App]', 'org.1invalid.App',
                    'org.invalid-prefix.App', 'org.test.' + 'a' * 250):
            with self.subTest(app=app), self.assertRaisesRegex(ValueError, 'application ID'):
                (self.directory / flatpaks.LISTS[0]).write_text(app)
                flatpaks.read_lists(self.directory)
        (self.directory / flatpaks.LISTS[0]).write_text('org.example.my-app')
        self.assertEqual(flatpaks.read_lists(self.directory)[0], ['org.example.my-app'])

    def test_add_and_remove_conflict_fails_before_build(self):
        for filename in flatpaks.LISTS:
            (self.directory / filename).write_text('org.mozilla.firefox')
        with self.assertRaisesRegex(ValueError, 'same Flatpak app'):
            flatpaks.read_lists(self.directory)

    def test_native_defaults_cover_additions_and_inherited_removals(self):
        config = configparser.ConfigParser()
        config.read_string(flatpaks.declaration(['org.mozilla.firefox'], ['com.github.tchx84.Flatseal']))
        install = config['Flatpak Preinstall org.mozilla.firefox']
        remove = config['Flatpak Preinstall com.github.tchx84.Flatseal']
        self.assertEqual(install['Branch'], 'stable')
        self.assertEqual(install['CollectionID'], 'org.flathub.Stable')
        self.assertTrue(install.getboolean('Install'))
        self.assertFalse(remove.getboolean('Install'))

    def test_empty_lists_keep_parent_seed_and_do_no_download(self):
        root = self.directory / 'image'
        seed = root / flatpaks.SEED / '.ostree/repo'
        seed.mkdir(parents=True)
        inherited = seed / 'parent-content'
        inherited.write_text('existing image apps')
        flatpaks.build(self.directory, root=root,
                      runner=lambda *args, **kwargs: self.fail('Empty lists must not invoke Flatpak.'))
        self.assertEqual(inherited.read_text(), 'existing image apps')
        self.assertTrue((root / flatpaks.PREINSTALL).is_file())

    def test_personal_choices_have_one_group_and_keep_other_defaults(self):
        defaults = self.directory / 'preinstall.d'
        defaults.mkdir()
        inherited = defaults / 'avethyos.preinstall'
        inherited.write_text(flatpaks.declaration(
            ['com.github.tchx84.Flatseal', 'io.github.kolunmi.Bazaar'], []))
        flatpaks.write_defaults(['org.mozilla.firefox'], ['com.github.tchx84.Flatseal'], defaults)
        self.assertNotIn('[Flatpak Preinstall com.github.tchx84.Flatseal]', inherited.read_text())
        self.assertIn('[Flatpak Preinstall io.github.kolunmi.Bazaar]', inherited.read_text())
        self.assertIn('CollectionID=org.flathub.Stable', inherited.read_text())
        personal = defaults / flatpaks.PREINSTALL.name
        self.assertIn('[Flatpak Preinstall com.github.tchx84.Flatseal]', personal.read_text())
        self.assertIn('Install=false', personal.read_text())

    def test_downloads_use_temporary_installation_and_extend_seed(self):
        (self.directory / flatpaks.LISTS[0]).write_text('org.mozilla.firefox')
        root = self.directory / 'image'
        parent_seed = root / flatpaks.SEED / '.ostree/repo/inherited'
        parent_seed.parent.mkdir(parents=True)
        parent_seed.write_text('keep this')
        calls = []
        environments = []

        def runner(command, **kwargs):
            calls.append(command)
            environments.append(kwargs['env'])
            self.assertTrue(kwargs['check'])
            config = root / f'etc/flatpak/installations.d/{flatpaks.INSTALLATION}.conf'
            self.assertTrue(config.exists())
            self.assertNotIn('/var/lib/flatpak', config.read_text())
            self.assertIn(str(root / 'var/tmp'), config.read_text())
            return subprocess.CompletedProcess(command, 0, stdout='[Application]\nname=org.mozilla.firefox\n')

        flatpaks.build(self.directory, root=root, runner=runner)
        self.assertEqual(calls[0][0], 'bwrap')
        self.assertTrue(all(command[:2] == ['flatpak', '--installation=avethyos-personal-build']
                            for command in calls[1:]))
        install = next(command for command in calls if 'install' in command)
        self.assertEqual(install[-2:], ['flathub', 'org.mozilla.firefox//stable'])
        self.assertIn(f'--sideload-repo={parent_seed.parent}', install)
        export = next(command for command in calls if 'create-usb' in command)
        self.assertIn(str(root / flatpaks.SEED), export)
        self.assertEqual(parent_seed.read_text(), 'keep this')
        self.assertFalse((root / f'etc/flatpak/installations.d/{flatpaks.INSTALLATION}.conf').exists())
        self.assertEqual(list((root / 'var/tmp').iterdir()), [])
        for env in environments:
            self.assertFalse(Path(env['XDG_RUNTIME_DIR']).exists())
            self.assertTrue(env['FLATPAK_TRIGGERSDIR'].startswith(str(root / 'var/tmp')))

    def test_download_failure_retries_and_cleans_build_state(self):
        (self.directory / flatpaks.LISTS[0]).write_text('org.mozilla.firefox')
        root = self.directory / 'image'
        failures = []
        pauses = []

        def runner(command, **kwargs):
            if 'install' in command:
                failures.append(command)
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)

        with self.assertRaises(subprocess.CalledProcessError):
            flatpaks.build(self.directory, root=root, runner=runner, pause=pauses.append)
        self.assertEqual(len(failures), 5)
        self.assertEqual(pauses, [10, 20, 30, 40])
        self.assertFalse((root / f'etc/flatpak/installations.d/{flatpaks.INSTALLATION}.conf').exists())
        self.assertEqual(list((root / 'var/tmp').iterdir()), [])

    def test_extra_data_requires_a_source_uri(self):
        self.assertFalse(flatpaks.has_extra_data('[Application]\nuri=https://example.invalid\n'))
        self.assertFalse(flatpaks.has_extra_data('[Extra Data]\nNoRuntime=true\n'))
        self.assertFalse(flatpaks.has_extra_data('[Extra Data]\nuri=\n'))
        self.assertTrue(flatpaks.has_extra_data('[Extra Data]\nuri=https://example.invalid/extra\n'))
        self.assertTrue(flatpaks.has_extra_data('[Extra Data]\nuri0=https://example.invalid/one\n'
                                              'uri1=https://example.invalid/two\n'))

    def test_extra_data_apps_keep_defaults_and_do_not_break_offline_exports(self):
        for apps in ('com.spotify.Client\norg.mozilla.firefox', 'com.spotify.Client'):
            with self.subTest(apps=apps):
                (self.directory / flatpaks.LISTS[0]).write_text(apps)
                root = self.directory / 'image'
                calls = []

                def runner(command, **kwargs):
                    calls.append(command)
                    metadata = ('[Extra Data]\nuri=https://example.invalid/extra\n'
                                if command[-1] == 'com.spotify.Client//stable' else '[Application]\n')
                    return subprocess.CompletedProcess(command, 0, stdout=metadata)

                flatpaks.build(self.directory, root=root, runner=runner)
                install = next(command for command in calls if 'install' in command)
                self.assertIn('com.spotify.Client//stable', install)
                defaults = (root / flatpaks.PREINSTALL).read_text()
                self.assertIn('[Flatpak Preinstall com.spotify.Client]', defaults)
                self.assertIn('Install=true', defaults)
                exports = [command for command in calls if 'create-usb' in command]
                if 'org.mozilla.firefox' in apps:
                    self.assertEqual(len(exports), 1)
                    self.assertEqual(exports[0][-1], 'org.mozilla.firefox//stable')
                    self.assertNotIn('com.spotify.Client//stable', exports[0])
                else:
                    self.assertEqual(exports, [])

    def test_sandbox_failure_is_not_retried_as_a_download(self):
        (self.directory / flatpaks.LISTS[0]).write_text('com.spotify.Client')
        root = self.directory / 'image'
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            raise subprocess.CalledProcessError(1, command)

        with self.assertRaises(subprocess.CalledProcessError):
            flatpaks.build(self.directory, root=root, runner=runner,
                          pause=lambda seconds: self.fail('A sandbox failure cannot be fixed by retrying.'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'bwrap')
        self.assertFalse((root / f'etc/flatpak/installations.d/{flatpaks.INSTALLATION}.conf').exists())
        self.assertEqual(list((root / 'var/tmp').iterdir()), [])


@unittest.skipUnless(shutil.which('flatpak') and shutil.which('ostree') and shutil.which('gpg'),
                     'Native lifecycle check needs Flatpak 1.18+, OSTree and GPG.')
class NativeFlatpakLifecycleTests(unittest.TestCase):
    def test_native_sync_seed_and_user_choices(self):
        with tempfile.TemporaryDirectory(prefix='avethyos-flatpak-test-') as temporary:
            work = Path(temporary)
            env = dict(os.environ, HOME=str(work / 'home'), XDG_DATA_HOME=str(work / 'data-home'),
                       XDG_CACHE_HOME=str(work / 'cache'), XDG_RUNTIME_DIR=str(work / 'runtime'),
                       XDG_CONFIG_HOME=str(work / 'config-home'), FLATPAK_USER_DIR=str(work / 'installation'),
                       FLATPAK_SYSTEM_DIR=str(work / 'unused-system'), FLATPAK_CONFIG_DIR=str(work / 'config'),
                       FLATPAK_DATA_DIR=str(work / 'defaults'), FLATPAK_TRIGGERSDIR=str(work / 'triggers'),
                       GNUPGHOME=str(work / 'gpg'))
            for key in ('HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR',
                        'XDG_CONFIG_HOME', 'FLATPAK_CONFIG_DIR', 'FLATPAK_DATA_DIR', 'FLATPAK_TRIGGERSDIR', 'GNUPGHOME'):
                Path(env[key]).mkdir(mode=0o700)

            def run(*args, environment=env):
                result = subprocess.run(args, env=environment, text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            help_result = subprocess.run(['flatpak', 'preinstall', '--help'], env=env,
                                         text=True, capture_output=True, check=False)
            if help_result.returncode:
                self.skipTest('Installed Flatpak lacks native preinstall support.')
            run('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                '--quick-generate-key', 'Flatpak fixture <test@example.invalid>', 'ed25519', 'sign', '0')
            key = next(line.split(':')[9] for line in run('gpg', '--with-colons', '--list-secret-keys').splitlines()
                       if line.startswith('fpr:'))
            public_key = work / 'key.gpg'
            with public_key.open('wb') as stream:
                subprocess.run(['gpg', '--export', key], env=env, stdout=stream, check=True)
            arch = run('flatpak', '--default-arch').strip()
            platform = 'org.avethyos.TestPlatform'
            baseline, personal, separate = ('org.avethyos.TestBaseline', 'org.avethyos.TestPersonal',
                                             'org.avethyos.TestSeparate')
            repository = work / 'repository'
            for name, is_runtime in ((platform, True), (baseline, False), (personal, False), (separate, False)):
                build = work / name
                (build / 'files/bin').mkdir(parents=True)
                executable = build / 'files/bin/example'
                executable.write_text('#!/bin/sh\nexit 0\n')
                executable.chmod(0o755)
                kind = 'Runtime' if is_runtime else 'Application'
                (build / 'metadata').write_text(
                    f'[{kind}]\nname={name}\nruntime={platform}/{arch}/stable\n'
                    f'sdk={platform}/{arch}/stable\ncommand=example\n')
                if not is_runtime:
                    run('flatpak', 'build-finish', str(build))
                options = ['--runtime'] if is_runtime else []
                run('flatpak', 'build-export', '--disable-sandbox', '--files=files', f'--collection-id={flatpaks.COLLECTION}',
                    f'--gpg-sign={key}', f'--gpg-homedir={env["GNUPGHOME"]}',
                    *options, str(repository), str(build), 'stable')
            run('flatpak', 'remote-add', '--user', f'--gpg-import={public_key}',
                f'--collection-id={flatpaks.COLLECTION}', 'fixture', repository.as_uri())
            defaults = Path(env['FLATPAK_DATA_DIR']) / 'preinstall.d'
            defaults.mkdir()
            (defaults / 'avethyos.preinstall').write_text(flatpaks.declaration([baseline], []))
            flatpaks.write_defaults([personal], [], defaults)
            run('flatpak', 'preinstall', '--user', '-y', '--noninteractive')

            def installed():
                return set(run('flatpak', 'list', '--user', '--app', '--columns=application').splitlines())

            self.assertEqual(installed(), {baseline, personal})
            seed = work / 'seed'
            seed.mkdir()
            run('flatpak', 'create-usb', '--user', str(seed), baseline)
            run('flatpak', 'create-usb', '--user', str(seed), personal)
            refs = run('ostree', f'--repo={seed / ".ostree/repo"}', 'refs', '--collections')
            self.assertIn(f'app/{baseline}/{arch}/stable', refs)
            self.assertIn(f'app/{personal}/{arch}/stable', refs)
            self.assertIn(f'runtime/{platform}/{arch}/stable', refs)

            # A fresh installation can use the combined seed with the original
            # remote unavailable, as an offline ISO installation does.
            offline_env = dict(env, FLATPAK_USER_DIR=str(work / 'offline-installation'))
            run('flatpak', 'remote-add', '--user', f'--gpg-import={public_key}',
                f'--collection-id={flatpaks.COLLECTION}', 'fixture', repository.as_uri(), environment=offline_env)
            offline_source = work / 'unavailable-repository'
            repository.rename(offline_source)
            try:
                run('flatpak', 'preinstall', '--user', '-y', '--noninteractive',
                    f'--sideload-repo={seed / ".ostree/repo"}', environment=offline_env)
                offline_apps = run('flatpak', 'list', '--user', '--app', '--columns=application',
                                   environment=offline_env)
                self.assertEqual(set(offline_apps.splitlines()), {baseline, personal})
            finally:
                offline_source.rename(repository)

            # A separately installed app and saved data survive image removals.
            run('flatpak', 'install', '--user', '-y', '--noninteractive', 'fixture', separate)
            saved_data = work / f'home/.var/app/{baseline}/saved-file'
            saved_data.parent.mkdir(parents=True)
            saved_data.write_text('keep my data')
            flatpaks.write_defaults([personal], [baseline, separate], defaults)
            run('flatpak', 'preinstall', '--user', '-y', '--noninteractive')
            self.assertEqual(installed(), {personal, separate})
            self.assertEqual(saved_data.read_text(), 'keep my data')

            # Native tracking also remembers a user's uninstall on later boots.
            run('flatpak', 'uninstall', '--user', '-y', '--noninteractive', personal)
            run('flatpak', 'preinstall', '--user', '-y', '--noninteractive')
            self.assertEqual(installed(), {separate})
            flatpaks.write_defaults([], [baseline], defaults)
            run('flatpak', 'preinstall', '--user', '-y', '--noninteractive')
            self.assertEqual(installed(), {separate})


if __name__ == '__main__':
    unittest.main()
