#!/usr/bin/python3
"""Verify the just-published personal image before building its installer."""

import json
import os
import re
import subprocess
import sys

from upstream import configuration, output


def verified_image(metadata, image, digest, variant, revision, repository):
    labels = metadata.get('Labels') or {}
    if (not re.fullmatch(r'sha256:[a-f0-9]{64}', digest)
            or metadata.get('Digest') != digest
            or metadata.get('Os') != 'linux' or metadata.get('Architecture') != 'amd64'
            or labels.get('org.opencontainers.image.revision') != revision
            or labels.get('org.opencontainers.image.source') != f'https://github.com/{repository}'
            or labels.get('io.avethyos.variant') != variant
            or labels.get('containers.bootc') != '1'):
        raise ValueError('The installer image must match the published digest, customization and variant.')
    return f'{image}@{digest}'


def installer_name(name, variant, run_id, attempt):
    if (not run_id.isdigit() or int(run_id) < 1
            or not attempt.isdigit() or int(attempt) < 1):
        raise ValueError('The installer must identify its successful build run and attempt.')
    return f'{name}-{variant}-{run_id}-{attempt}'


def main():
    config = configuration()
    repository = os.environ['GITHUB_REPOSITORY']
    image = f'ghcr.io/{repository.split("/")[0].lower()}/{config["IMAGE_NAME"]}'
    if os.environ['IMAGE_REF'] != image or os.environ['VARIANT'] != config['VARIANT']:
        raise ValueError('The installer does not match this repository and image.conf.')
    digest = os.environ['DIGEST']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise ValueError('The published image digest is invalid.')
    inspection = subprocess.run([
        'skopeo', 'inspect', '--retry-times=3', '--authfile', os.environ['REGISTRY_AUTH_FILE'],
        '--override-os=linux', '--override-arch=amd64', f'docker://{image}@{digest}'],
        check=True, capture_output=True, text=True)
    source = verified_image(json.loads(inspection.stdout), image, digest, config['VARIANT'],
                            os.environ['GITHUB_SHA'], repository)
    artifact = installer_name(config['IMAGE_NAME'], config['VARIANT'],
                              os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'])
    output({'artifact': artifact})
    print(f'Installer payload: {source}; future updates: {image}:latest.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
