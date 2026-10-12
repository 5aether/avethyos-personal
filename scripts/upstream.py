#!/usr/bin/python3
"""Choose an exact successful AvethyOS release and avoid unchanged rebuilds."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import urllib.request

UPSTREAM = '5aether/avethyos'
TEMPLATE = '5aether/avethyos-personal'
WORKFLOW = '.github/workflows/build.yml'
WORKFLOW_NAME = 'Build and verify AvethyOS'
BASE_IMAGE = 'ghcr.io/5aether/avethyos'
PROMOTION = 'Promote the verified digest to update tags'


def configuration(path=Path('image.conf')):
    config = {}
    for line in path.read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        key, separator, value = line.partition('=')
        if not separator or key.strip() not in ('VARIANT', 'IMAGE_NAME') or key.strip() in config:
            raise ValueError('image.conf accepts one VARIANT and one IMAGE_NAME setting.')
        config[key.strip()] = value.strip()
    if config.get('VARIANT') not in ('standard', 'nvidia'):
        raise ValueError('Choose VARIANT=standard or VARIANT=nvidia in image.conf.')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', config.get('IMAGE_NAME', '')):
        raise ValueError('IMAGE_NAME must contain lowercase letters, digits and single hyphens.')
    return config


def check_customization():
    for name in ('packages.txt', 'remove-packages.txt'):
        for line in Path(name).read_text().splitlines():
            package = line.split('#', 1)[0].strip()
            if package and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+:-]*', package):
                raise ValueError(f'{name}: put one package name on each line.')
    with Path('kernel-arguments.toml').open('rb') as stream:
        settings = tomllib.load(stream)
    if 'kargs' not in settings:
        raise ValueError('kernel-arguments.toml needs kargs = [], or a list of your arguments.')
    if set(settings) - {'kargs', 'match-architectures'}:
        raise ValueError('kernel-arguments.toml accepts kargs and optional match-architectures.')
    for key in settings:
        if not isinstance(settings[key], list) or any(not isinstance(arg, str) or not arg
                or any(character.isspace() for character in arg) for arg in settings[key]):
            raise ValueError(f'{key} must be a list of nonempty strings without whitespace.')
    subprocess.run(['bash', '-n', 'build.sh'], check=True)


def api(path):
    headers = {'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    token = os.environ.get('GH_TOKEN', '')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(f'https://api.github.com/repos/{UPSTREAM}/{path}', headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def published_run(run):
    return (run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and run.get('event') in ('push', 'schedule', 'workflow_dispatch')
            and run.get('head_branch') == 'main' and run.get('path') == WORKFLOW
            and run.get('name') == WORKFLOW_NAME
            and run.get('repository', {}).get('full_name', '').lower() == UPSTREAM
            and run.get('head_repository', {}).get('full_name', '').lower() == UPSTREAM
            and re.fullmatch(r'[a-f0-9]{40}', run.get('head_sha', '')) is not None)


def latest_successful_run(fetch=api):
    # Pagination also handles long stretches of successful PR checks.
    for page in range(1, 11):
        runs = fetch(f'actions/workflows/build.yml/runs?branch=main&status=success&per_page=100&page={page}')['workflow_runs']
        eligible = [run for run in runs if published_run(run)]
        if eligible:
            return max(eligible, key=lambda run: (run['run_number'], run['run_attempt']))
        if len(runs) < 100:
            break
    return None


def dispatched_run(payload, fetch=api):
    if payload.get('upstream_repository') != UPSTREAM:
        raise ValueError('This dispatch did not identify the AvethyOS repository.')
    run_id, attempt = payload.get('run_id'), payload.get('run_attempt')
    if type(run_id) is not int or type(attempt) is not int or run_id < 1 or attempt < 1:
        raise ValueError('This dispatch did not identify a valid upstream run.')
    run = fetch(f'actions/runs/{run_id}/attempts/{attempt}')
    return run if published_run(run) else None


def verify_publication(run, fetch=api):
    jobs = fetch(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")['jobs']
    for variant in ('standard', 'nvidia'):
        matches = [job for job in jobs if job.get('name') == f'Validate and release {variant}']
        if len(matches) != 1 or matches[0].get('conclusion') != 'success' or not any(
                step.get('name') == PROMOTION and step.get('conclusion') == 'success'
                for step in matches[0].get('steps', [])):
            raise ValueError('Both AvethyOS variants must finish publishing before a personal build.')


def inspect(reference, authfile, optional=False, runner=subprocess.run):
    result = runner(['skopeo', 'inspect', '--retry-times=3', '--override-os=linux',
                     '--override-arch=amd64', '--authfile', authfile, f'docker://{reference}'],
                    text=True, capture_output=True, check=False)
    if result.returncode:
        if optional:
            return None
        raise RuntimeError(f'Cannot inspect the selected published image: {result.stderr.strip()}')
    return json.loads(result.stdout)


def verified_base(run, variant, metadata):
    digest = metadata.get('Digest', '')
    labels = metadata.get('Labels') or {}
    if (not re.fullmatch(r'sha256:[a-f0-9]{64}', digest)
            or metadata.get('Os') != 'linux' or metadata.get('Architecture') != 'amd64'
            or labels.get('org.opencontainers.image.revision') != run['head_sha']
            or labels.get('org.opencontainers.image.source') != f'https://github.com/{UPSTREAM}'
            or labels.get('io.avethyos.variant') != variant):
        raise ValueError('The published base image does not match the successful upstream run and variant.')
    return f'{BASE_IMAGE}@{digest}'


def unchanged(metadata, digest, revision, variant):
    labels = (metadata or {}).get('Labels') or {}
    return (labels.get('io.avethyos.upstream.digest') == digest
            and labels.get('org.opencontainers.image.revision') == revision
            and labels.get('io.avethyos.variant') == variant)


def output(values):
    lines = ''.join(f'{key}={value}\n' for key, value in values.items())
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(lines)
    else:
        print(lines, end='')


def main():
    if os.environ.get('GITHUB_REPOSITORY', '').lower() in (UPSTREAM, TEMPLATE):
        output({'build': 'false'})
        return
    config = configuration()
    check_customization()
    if os.environ.get('GITHUB_EVENT_NAME') == 'repository_dispatch':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        notified = dispatched_run(event.get('client_payload', {}))
        run = latest_successful_run() if notified is not None else None
        # A delayed notification must never replace a newer successful release.
        if notified is not None and (run is None or notified['run_number'] > run['run_number']):
            run = notified
    else:
        run = latest_successful_run()
    if run is None:
        print('No completed, successful AvethyOS release is available. Nothing will be built.')
        output({'build': 'false'})
        return
    verify_publication(run)
    variant = config['VARIANT']
    candidate = f"{BASE_IMAGE}:build-{variant}-{run['id']}-{run['run_attempt']}"
    authfile = os.environ['REGISTRY_AUTH_FILE']
    base = verified_base(run, variant, inspect(candidate, authfile))
    digest = base.split('@', 1)[1]
    owner = os.environ['GITHUB_REPOSITORY_OWNER'].lower()
    image = f"ghcr.io/{owner}/{config['IMAGE_NAME']}"
    current = inspect(f'{image}:latest', authfile, optional=True)
    build = os.environ.get('FORCE_BUILD') == 'true' or not unchanged(current, digest, os.environ['GITHUB_SHA'], variant)
    output({'build': str(build).lower(), 'base': base, 'digest': digest, 'image': image,
            'variant': variant, 'upstream_run': run['id']})
    print(f"Selected successful AvethyOS run {run['id']}, {variant}, {digest}.")
    if not build:
        print('Your customization and the base digest are unchanged; nothing will be rebuilt.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
