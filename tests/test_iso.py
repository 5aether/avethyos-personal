"""Verify installer payload identity and private download planning without a build."""

from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('personal_iso', ROOT / 'scripts/iso.py')
iso = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iso)
SHA = 'a' * 40
DIGEST = 'sha256:' + 'b' * 64
IMAGE = 'ghcr.io/person/my-image'
REPOSITORY = 'person/my-files'
PAYLOAD = {'Digest': DIGEST, 'Os': 'linux', 'Architecture': 'amd64',
           'Labels': {'org.opencontainers.image.revision': SHA,
                      'org.opencontainers.image.source': f'https://github.com/{REPOSITORY}',
                      'io.avethyos.variant': 'standard'}}


class InstallerPayloadTests(unittest.TestCase):
    def test_only_the_selected_variant_and_published_digest_are_used(self):
        self.assertEqual(iso.verify_payload(PAYLOAD, IMAGE, DIGEST, 'standard', SHA, REPOSITORY),
                         f'{IMAGE}@{DIGEST}')
        nvidia = deepcopy(PAYLOAD)
        nvidia['Labels']['io.avethyos.variant'] = 'nvidia'
        self.assertEqual(iso.verify_payload(nvidia, IMAGE, DIGEST, 'nvidia', SHA, REPOSITORY),
                         f'{IMAGE}@{DIGEST}')

    def test_a_different_digest_platform_revision_source_or_variant_is_rejected(self):
        for key, value in (('Digest', 'sha256:' + 'c' * 64),
                           ('Os', 'windows'), ('Architecture', 'arm64')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                iso.verify_payload(dict(PAYLOAD, **{key: value}), IMAGE, DIGEST, 'standard', SHA, REPOSITORY)
        for key in PAYLOAD['Labels']:
            payload = deepcopy(PAYLOAD)
            payload['Labels'][key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(ValueError):
                iso.verify_payload(payload, IMAGE, DIGEST, 'standard', SHA, REPOSITORY)

    def test_private_image_is_inspected_by_digest_with_the_explicit_authfile(self):
        environment = {'IMAGE_REF': IMAGE, 'DIGEST': DIGEST, 'VARIANT': 'standard',
                       'REGISTRY_AUTH_FILE': '/tmp/isolated-auth.json', 'GITHUB_SHA': SHA,
                       'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_REPOSITORY_OWNER': 'person',
                       'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '2'}
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(iso, 'configuration', return_value={'VARIANT': 'standard', 'IMAGE_NAME': 'my-image'}), \
                patch.object(iso, 'inspect', return_value=PAYLOAD) as inspect, \
                patch.object(iso, 'output') as output:
            iso.main()
            inspect.assert_called_once_with(f'{IMAGE}@{DIGEST}', '/tmp/isolated-auth.json')
            output.assert_called_once_with({'artifact': 'my-image-standard-42-2'})

    def test_changed_configuration_cannot_build_a_different_image(self):
        environment = {'IMAGE_REF': IMAGE, 'DIGEST': DIGEST, 'VARIANT': 'nvidia',
                       'GITHUB_REPOSITORY_OWNER': 'person'}
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(iso, 'configuration', return_value={'VARIANT': 'standard', 'IMAGE_NAME': 'my-image'}), \
                patch.object(iso, 'inspect') as inspect:
            with self.assertRaises(ValueError):
                iso.main()
            inspect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
