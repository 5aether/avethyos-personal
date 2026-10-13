import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('personal_iso', ROOT / 'scripts/iso.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.repository = 'owner/private-avethyos'
        self.image = 'ghcr.io/owner/private-avethyos'
        self.digest = 'sha256:' + 'b' * 64
        self.revision = 'a' * 40
        self.metadata = {'Digest': self.digest, 'Os': 'linux', 'Architecture': 'amd64',
                         'Labels': {'org.opencontainers.image.revision': self.revision,
                                    'org.opencontainers.image.source': f'https://github.com/{self.repository}',
                                    'io.avethyos.variant': 'standard', 'containers.bootc': '1'}}

    def verify(self, metadata):
        return installer.verified_image(metadata, self.image, self.digest, 'standard',
                                        self.revision, self.repository)

    def test_exact_published_digest_is_used(self):
        self.assertEqual(self.verify(self.metadata), self.image + '@' + self.digest)

    def test_wrong_digest_platform_customization_and_variant_are_rejected(self):
        for field, value in (('Digest', 'sha256:' + 'c' * 64), ('Os', 'windows'),
                             ('Architecture', 'arm64')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(dict(self.metadata, **{field: value}))
        for field, value in (('org.opencontainers.image.revision', 'c' * 40),
                             ('org.opencontainers.image.source', 'https://github.com/foreign/repo'),
                             ('io.avethyos.variant', 'nvidia'), ('containers.bootc', '0')):
            metadata = copy.deepcopy(self.metadata)
            metadata['Labels'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(metadata)

    def test_each_successful_build_gets_a_distinct_installer_name(self):
        self.assertEqual(installer.installer_name('personal', 'nvidia', '123', '2'),
                         'personal-nvidia-123-2')
        for run, attempt in (('0', '1'), ('1', '0'), ('x', '1'), ('1', '../2')):
            with self.assertRaises(ValueError):
                installer.installer_name('personal', 'standard', run, attempt)

    def test_private_digest_pull_keeps_credentials_out_of_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            fake_podman = temporary / 'podman'
            fake_podman.write_text('''#!/usr/bin/python3
import json, os
from pathlib import Path
import sys
args = sys.argv[1:]
with open(os.environ['TEST_PODMAN_LOG'], 'a') as output:
    output.write(json.dumps(args) + '\\n')
if args[:2] == ['image', 'exists']:
    sys.exit(1)
if args[0] == 'run':
    mount = next(args[index + 1] for index, value in enumerate(args)
                 if value == '-v' and args[index + 1].endswith(':/output'))
    output = Path(mount.removesuffix(':/output')) / 'bootiso'
    output.mkdir()
    (output / 'install.iso').write_bytes(b'isolated installer fixture')
''')
            fake_podman.chmod(0o755)
            fake_sudo = temporary / 'sudo'
            fake_sudo.write_text('#!/bin/sh\nexec "$@"\n')
            fake_sudo.chmod(0o755)
            authfile = temporary / 'auth.json'
            authfile.write_text('{"auths":{"ghcr.io":{"auth":"fixture"}}}')
            output = temporary / 'output'
            log = temporary / 'commands.jsonl'
            source = self.image + '@' + self.digest
            environment = dict(os.environ, PATH=f'{temporary}:{os.environ["PATH"]}',
                               TEST_PODMAN_LOG=str(log), REGISTRY_AUTH_FILE=str(authfile),
                               ISO_SOURCE_REF=source)
            subprocess.run(['bash', 'scripts/build-iso.sh', self.image, 'latest', str(output)],
                           cwd=ROOT, env=environment, check=True, capture_output=True, text=True)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertIn(['pull', '--retry=3', '--authfile', str(authfile), source], commands)
            self.assertIn(['tag', source, self.image + ':latest'], commands)
            builder = next(command for command in commands if command[0] == 'run')
            self.assertEqual(builder[-1], self.image + ':latest')
            self.assertNotIn(str(authfile), ' '.join(builder))
            self.assertNotIn('--authfile', builder)
            self.assertEqual((output / 'bootiso/install.iso').read_bytes(),
                             b'isolated installer fixture')


if __name__ == '__main__':
    unittest.main()
