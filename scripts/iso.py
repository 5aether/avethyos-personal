#!/usr/bin/python3
"""Check the newly published personal payload before building its installer."""

import os
import re
import sys

from upstream import configuration, inspect, output


def verify_payload(metadata, image, digest, variant, revision, repository):
    labels = metadata.get('Labels') or {}
    if (not re.fullmatch(r'sha256:[a-f0-9]{64}', digest)
            or metadata.get('Digest') != digest
            or metadata.get('Os') != 'linux' or metadata.get('Architecture') != 'amd64'
            or labels.get('org.opencontainers.image.revision') != revision
            or labels.get('org.opencontainers.image.source') != f'https://github.com/{repository}'
            or labels.get('io.avethyos.variant') != variant):
        raise ValueError('The installer payload does not match the personal image just published.')
    return f'{image}@{digest}'


def main():
    config = configuration()
    image, digest, variant = (os.environ[key] for key in ('IMAGE_REF', 'DIGEST', 'VARIANT'))
    expected_image = f"ghcr.io/{os.environ['GITHUB_REPOSITORY_OWNER'].lower()}/{config['IMAGE_NAME']}"
    if image != expected_image or variant != config['VARIANT']:
        raise ValueError('The installer must use your configured image name and single graphics variant.')
    verify_payload(inspect(f'{image}@{digest}', os.environ['REGISTRY_AUTH_FILE']),
                   image, digest, variant, os.environ['GITHUB_SHA'], os.environ['GITHUB_REPOSITORY'])
    output({'artifact': f"{config['IMAGE_NAME']}-{variant}-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"})
    print(f'Installer verified: {image}@{digest}. Updates will follow {image}:latest.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
