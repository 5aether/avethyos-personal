"""Exercise release gates and private registry planning without changing a system."""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


upstream = module('personal_upstream', ROOT / 'scripts/upstream.py')
SHA = 'a' * 40
DIGEST = 'sha256:' + 'b' * 64
RUN = {'id': 42, 'run_attempt': 2, 'run_number': 10, 'status': 'completed',
       'conclusion': 'success', 'event': 'schedule', 'head_branch': 'main',
       'path': upstream.WORKFLOW, 'name': upstream.WORKFLOW_NAME, 'head_sha': SHA,
       'repository': {'full_name': upstream.UPSTREAM},
       'head_repository': {'full_name': upstream.UPSTREAM}}
BASE = {'Digest': DIGEST, 'Os': 'linux', 'Architecture': 'amd64',
        'Labels': {'org.opencontainers.image.revision': SHA,
                   'org.opencontainers.image.source': 'https://github.com/' + upstream.UPSTREAM,
                   'io.avethyos.variant': 'standard'}}
JOBS = [{'name': f'Validate and release {variant}', 'conclusion': 'success',
         'steps': [{'name': upstream.PROMOTION, 'conclusion': 'success'}]}
        for variant in ('standard', 'nvidia')]


class UpstreamGateTests(unittest.TestCase):
    def test_only_completed_main_releases_are_eligible(self):
        self.assertTrue(upstream.published_run(RUN))
        for key, value in (('conclusion', 'failure'), ('conclusion', 'cancelled'),
                           ('status', 'in_progress'), ('event', 'pull_request'),
                           ('event', 'merge_group'), ('head_branch', 'personal'),
                           ('path', '.github/workflows/build-disk.yml'),
                           ('name', 'Build container image'), ('head_sha', 'bad')):
            with self.subTest(key=key, value=value):
                rejected = dict(RUN, **{key: value})
                self.assertFalse(upstream.published_run(rejected))
        for key in ('repository', 'head_repository'):
            with self.subTest(key=key):
                self.assertFalse(upstream.published_run(dict(RUN, **{key: {'full_name': 'someone/avethyos'}})))

    def test_latest_success_filters_old_workflows_and_prs(self):
        newer = dict(RUN, id=43, run_number=11)
        pr = dict(RUN, id=99, run_number=12, event='pull_request')
        old = dict(RUN, id=100, run_number=13, name='Build container image')
        selected = upstream.latest_successful_run(lambda path: {'workflow_runs': [old, pr, RUN, newer]})
        self.assertEqual(selected['id'], 43)

    def test_no_modern_success_skips(self):
        old = dict(RUN, name='Build container image')
        self.assertIsNone(upstream.latest_successful_run(lambda path: {'workflow_runs': [old]}))

    def test_one_failed_variant_or_skipped_publication_blocks_customization(self):
        upstream.verify_publication(RUN, lambda path: {'jobs': JOBS})
        for jobs in (JOBS[:1], [dict(JOBS[0], conclusion='failure'), JOBS[1]],
                     [dict(JOBS[0], steps=[]), JOBS[1]],
                     [dict(JOBS[0], steps=[{'name': upstream.PROMOTION, 'conclusion': 'skipped'}]), JOBS[1]]):
            with self.subTest(jobs=jobs), self.assertRaisesRegex(ValueError, 'Both AvethyOS'):
                upstream.verify_publication(RUN, lambda path: {'jobs': jobs})

    def test_dispatch_uses_exact_attempt_and_rechecks_failure(self):
        calls = []
        def fetch(path):
            calls.append(path)
            return RUN
        payload = {'upstream_repository': upstream.UPSTREAM, 'run_id': 42, 'run_attempt': 2}
        self.assertEqual(upstream.dispatched_run(payload, fetch), RUN)
        self.assertEqual(calls, ['actions/runs/42/attempts/2'])
        for run in (dict(RUN, conclusion='failure'), dict(RUN, status='in_progress')):
            self.assertIsNone(upstream.dispatched_run(payload, lambda path: run))

    def test_invalid_dispatch_never_queries_arbitrary_repository_or_path(self):
        payload = {'upstream_repository': upstream.UPSTREAM, 'run_id': 42, 'run_attempt': 2}
        for changes in ({'upstream_repository': 'someone/fork'}, {'run_id': '../secrets'},
                        {'run_id': True}, {'run_attempt': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                upstream.dispatched_run(dict(payload, **changes), lambda path: self.fail(path))

    def test_standard_and_nvidia_are_pinned_separately(self):
        self.assertEqual(upstream.verified_base(RUN, 'standard', BASE), upstream.BASE_IMAGE + '@' + DIGEST)
        nvidia = deepcopy(BASE)
        nvidia['Labels']['io.avethyos.variant'] = 'nvidia'
        self.assertEqual(upstream.verified_base(RUN, 'nvidia', nvidia), upstream.BASE_IMAGE + '@' + DIGEST)
        with self.assertRaises(ValueError):
            upstream.verified_base(RUN, 'standard', nvidia)

    def test_candidate_revision_source_platform_and_digest_are_checked(self):
        for key, value in (('Digest', 'sha256:bad'), ('Os', 'windows'), ('Architecture', 'arm64')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                upstream.verified_base(RUN, 'standard', dict(BASE, **{key: value}))
        for key in ('org.opencontainers.image.revision', 'org.opencontainers.image.source'):
            candidate = deepcopy(BASE)
            candidate['Labels'][key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(ValueError):
                upstream.verified_base(RUN, 'standard', candidate)

    def test_unchanged_digest_and_personal_commit_skip(self):
        current = {'Labels': {'io.avethyos.upstream.digest': DIGEST,
                             'org.opencontainers.image.revision': SHA,
                             'io.avethyos.variant': 'standard'}}
        self.assertTrue(upstream.unchanged(current, DIGEST, SHA, 'standard'))
        self.assertFalse(upstream.unchanged(current, 'sha256:' + 'c' * 64, SHA, 'standard'))
        self.assertFalse(upstream.unchanged(current, DIGEST, 'd' * 40, 'standard'))
        self.assertFalse(upstream.unchanged(current, DIGEST, SHA, 'nvidia'))
        self.assertFalse(upstream.unchanged(None, DIGEST, SHA, 'standard'))

    def test_private_registry_inspection_uses_persistent_explicit_authfile(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(BASE), '')
        self.assertEqual(upstream.inspect('ghcr.io/private/image:latest', '/tmp/test-auth.json', runner=runner), BASE)
        self.assertEqual(calls[0][calls[0].index('--authfile') + 1], '/tmp/test-auth.json')
        self.assertEqual(calls[0][-1], 'docker://ghcr.io/private/image:latest')

    def test_missing_base_fails_but_missing_first_personal_image_builds(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, '', 'manifest unknown')
        self.assertIsNone(upstream.inspect('ghcr.io/private/new:latest', '/tmp/test-auth.json', optional=True, runner=runner))
        with self.assertRaisesRegex(RuntimeError, 'Cannot inspect'):
            upstream.inspect('ghcr.io/upstream/missing', '/tmp/test-auth.json', runner=runner)

    def test_main_os_repository_never_builds_personal_image(self):
        for repository in (upstream.UPSTREAM, upstream.UPSTREAM.upper()):
            with self.subTest(repository=repository), \
                    patch.dict(os.environ, {'GITHUB_REPOSITORY': repository}, clear=True), \
                    patch.object(upstream, 'output') as output, patch.object(upstream, 'api') as api:
                upstream.main()
                output.assert_called_once_with({'build': 'false'})
                api.assert_not_called()

    def test_separate_personal_repository_builds_its_customization(self):
        environment = {'GITHUB_REPOSITORY': '5aether/avethyos-personal',
                       'GITHUB_REPOSITORY_OWNER': '5aether', 'GITHUB_EVENT_NAME': 'push',
                       'GITHUB_SHA': SHA, 'REGISTRY_AUTH_FILE': '/tmp/test-auth.json'}
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(upstream, 'configuration', return_value={'VARIANT': 'standard', 'IMAGE_NAME': 'avethyos-personal'}), \
                patch.object(upstream, 'check_customization'), \
                patch.object(upstream, 'latest_successful_run', return_value=RUN), \
                patch.object(upstream, 'verify_publication') as verify, \
                patch.object(upstream, 'inspect', side_effect=[BASE, None]), \
                patch.object(upstream, 'output') as output:
            upstream.main()
            verify.assert_called_once_with(RUN)
            self.assertEqual(output.call_args.args[0]['build'], 'true')
            self.assertEqual(output.call_args.args[0]['image'], 'ghcr.io/5aether/avethyos-personal')

    def test_delayed_dispatch_does_not_roll_back_a_newer_release(self):
        latest = dict(RUN, id=43, run_number=11)
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / 'event.json'
            event_path.write_text(json.dumps({'client_payload': {}}))
            environment = {'GITHUB_REPOSITORY': 'person/my-image', 'GITHUB_REPOSITORY_OWNER': 'person',
                           'GITHUB_EVENT_NAME': 'repository_dispatch', 'GITHUB_EVENT_PATH': str(event_path),
                           'GITHUB_SHA': SHA, 'REGISTRY_AUTH_FILE': '/tmp/test-auth.json'}
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(upstream, 'configuration', return_value={'VARIANT': 'standard', 'IMAGE_NAME': 'my-image'}), \
                    patch.object(upstream, 'check_customization'), \
                    patch.object(upstream, 'dispatched_run', return_value=RUN), \
                    patch.object(upstream, 'latest_successful_run', return_value=latest), \
                    patch.object(upstream, 'verify_publication') as verify, \
                    patch.object(upstream, 'inspect', side_effect=[BASE, None]) as inspect, \
                    patch.object(upstream, 'output') as output:
                upstream.main()
                verify.assert_called_once_with(latest)
                self.assertEqual(inspect.call_args_list[0].args[0], upstream.BASE_IMAGE + ':build-standard-43-2')
                self.assertEqual(output.call_args.args[0]['upstream_run'], 43)


class TemplateConfigurationTests(unittest.TestCase):
    def configure(self, contents):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.conf'
            path.write_text(contents)
            return upstream.configuration(path)

    def test_one_clear_variant_choice(self):
        for variant in ('standard', 'nvidia'):
            self.assertEqual(self.configure(f'# comment\nVARIANT={variant}\nIMAGE_NAME=my-image\n')['VARIANT'], variant)
        for choice in ('both', 'standard,nvidia', 'standard nvidia', '$(touch /tmp/no)'):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                self.configure(f'VARIANT={choice}\nIMAGE_NAME=my-image\n')

    def test_duplicate_and_unknown_config_are_rejected(self):
        for extra in ('VARIANT=nvidia', 'UNSUPPORTED=true', 'source /tmp/file'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.configure('VARIANT=standard\nIMAGE_NAME=my-image\n' + extra)

    def test_package_and_kernel_checks_never_execute_the_build_hook(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                Path('packages.txt').write_text('gimp\n# comment\n')
                Path('remove-packages.txt').write_text('')
                Path('flatpak-packages.txt').write_text('')
                Path('remove-flatpak-packages.txt').write_text('')
                Path('kernel-arguments.toml').write_text('kargs = ["hid_apple.fnmode=2"]\n')
                Path('build.sh').write_text('touch hook-was-executed\n')
                upstream.check_customization()
                self.assertFalse(Path('hook-was-executed').exists())
                Path('packages.txt').write_text('--assumeyes\n')
                with self.assertRaisesRegex(ValueError, 'one package name'):
                    upstream.check_customization()
                Path('packages.txt').write_text('')
                for content in ('kargs = false', 'kargs = ["two arguments"]', '# missing kargs'):
                    Path('kernel-arguments.toml').write_text(content)
                    with self.subTest(content=content), self.assertRaises(ValueError):
                        upstream.check_customization()
                self.assertFalse(Path('hook-was-executed').exists())
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
