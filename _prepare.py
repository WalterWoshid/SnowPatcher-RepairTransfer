"""Prepare private payloads for New-ModPackage.ps1. Never opens the game directory."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import _bytecode as bytecode


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require_hash(data, expected, name):
    if sha(data) != expected:
        raise ValueError(f'Required original changed: {name}. Review the compiler before rebuilding.')


def prepare(work, baseline):
    if sys.flags.optimize:
        raise ValueError('Run Python without -O; the compiler needs its compatibility assertions.')
    if not (work / 'snowpatcher.source.json').is_file():
        raise ValueError('Missing staged package source.')
    inputs = work / 'inputs'
    for variant in ('release', 'profile', 'debug'):
        original_path = inputs / f'initial_{variant}.sslbundle'
        _, originals = bytecode.read_bundle(original_path)
        original_modules = dict(originals)
        if len(original_modules) != len(originals):
            raise ValueError('Duplicate original module names.')
        for name, expected in baseline['modules'][variant].items():
            require_hash(original_modules[name], expected, f'{variant}: {name}')
        prepared_path = work / 'compiled' / original_path.name
        bytecode.patch_bundle(original_path, prepared_path)
        _, prepared = bytecode.read_bundle(prepared_path)
        prepared_modules = dict(prepared)
        expected_additions = {
            bytecode.renamed(bytecode.BASE + stem, resource)
            for resource in ('parts', 'wheels') for stem in bytecode.ORIGINALS
        }
        if set(prepared_modules) - set(original_modules) != expected_additions:
            raise ValueError('Unexpected added script modules.')
        changed = {name for name, data in original_modules.items() if prepared_modules.get(name) != data}
        if changed != {bytecode.HUD}:
            raise ValueError('Unexpected changes outside the HUD module.')
        folder = work / 'payloads' / variant
        folder.mkdir(parents=True)
        for name in sorted(expected_additions | {bytecode.HUD}):
            (folder / (name.rsplit('.', 1)[1] + '.sslb')).write_bytes(prepared_modules[name])

    config = {}
    for name, expected in baseline['ui_config_sha256'].items():
        data = (inputs / name.split('/')[-1]).read_bytes()
        require_hash(data, expected, name)
        config[name] = data.decode('utf-8-sig')
    folder = work / 'payloads' / 'ui'
    folder.mkdir(parents=True)
    for resource in ('parts', 'wheels'):
        for stem in bytecode.ORIGINALS:
            text = bytecode.renamed(config[f'[ps]/{stem}.sso'], resource)
            text = text.replace('"REFUEL"', '"TRANSFER_' + resource.upper() + '"')
            if stem == 'popup_refuel_ui_view_new':
                if text.count('UiIconsLibFuelstationaltimg40') != 1:
                    raise ValueError('Refuel header icon changed.')
                icon = 'UiIconsLibRepairvalueimg40' if resource == 'parts' else 'UiIconsLibRepairwheelsimg40'
                text = text.replace('UiIconsLibFuelstationaltimg40', icon)
            (folder / (bytecode.renamed(stem, resource) + '.sso')).write_text(text, encoding='utf-8', newline='')
    source = json.loads((work / 'snowpatcher.source.json').read_text(encoding='utf-8-sig'))
    expected_payloads = {
        op['payload'].replace('{variant}', variant)
        for op in source['operations'] if 'payload' in op
        for variant in (source['variants'] if '{variant}' in op['payload'] else [''])
    }
    actual_payloads = {p.relative_to(work).as_posix() for p in (work / 'payloads').rglob('*') if p.is_file()}
    if actual_payloads != expected_payloads:
        raise ValueError('Prepared payloads do not match the package source.')
    print(f'Prepared {len(actual_payloads)} payloads; unrelated script modules preserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    args = parser.parse_args()
    prepare(args.work.resolve(), json.loads(args.baseline.read_text(encoding='utf-8')))


if __name__ == '__main__':
    try:
        main()
    except (AssertionError, KeyError, ValueError, OSError) as error:
        print(f'Payload preparation stopped: {error or "required game code changed"}', file=sys.stderr)
        raise SystemExit(1)
