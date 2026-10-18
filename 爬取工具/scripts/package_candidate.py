"""Package a tested prerelease without claiming final acceptance or publishing.

Public source/data exclude originals, DBs, environments and credentials. A private
portable seed ZIP can be produced separately under data/ for user-side acceptance.
Existing release artifacts are never overwritten.
"""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

from anhui_crawler import __version__
from anhui_crawler.common import CrawlerError, now, sha256, write_json, safe_path


def descriptor(path):
    return {'name': path.name, 'sha256': sha256(path.read_bytes()), 'byte_size': path.stat().st_size}


def xml_counts(path):
    suites = list(ET.parse(path).getroot().iter('testsuite'))
    counts = {name: sum(int(s.attrib.get(name, 0)) for s in suites) for name in ['tests', 'errors', 'failures', 'skipped']}
    if not counts['tests'] or any(counts[k] for k in ['errors', 'failures', 'skipped']):
        raise CrawlerError('Test evidence is incomplete or has failures/skips')
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--private-seed', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    release = root / 'release'
    wheel = release / f'anhui_public_data_crawler-{__version__}-py3-none-any.whl'
    receipt = release / f'receipt-{__version__}.json'
    source_zip = release / f'anhui-public-data-crawler-{__version__}.zip'
    data_zip = release / f'anhui-crawler-data-{__version__}.zip'
    if any(p.exists() for p in [receipt, source_zip, data_zip]):
        raise CrawlerError('Existing releases are immutable')
    evidence = args.evidence.resolve()
    baseline = json.loads((evidence / 'baseline.json').read_text())
    for original in baseline['old_release_files']:
        if descriptor(release / original['name'])['sha256'] != original['sha256']:
            raise CrawlerError('An earlier formal release changed')
    source_tests = xml_counts(evidence / 'source-tests.xml')
    installed_tests = xml_counts(evidence / 'installed-wheel-tests.xml')
    if source_tests != installed_tests:
        raise CrawlerError('Source and installed test suites differ')
    native = json.loads((evidence / 'native-mac/receipt.json').read_text())
    native_schedule = json.loads((evidence / 'schedulers/Darwin.json').read_text())
    digest = sha256(wheel.read_bytes())
    if any(r.get('status') != 'passed' or r.get('wheel_sha256') != digest for r in [native, native_schedule]):
        raise CrawlerError('Candidate wheel and Mac receipts do not agree')
    export = args.export.resolve()
    manifest = json.loads((export / 'manifest.json').read_text())
    quality = json.loads((export / 'quality.json').read_text())
    gate = json.loads((evidence / 'final-gate.json').read_text())['final_gate']
    if manifest['version'] != __version__ or quality['status'] != 'passed' or gate['wheel_sha256'] != digest:
        raise CrawlerError('Candidate data or final gate evidence is inconsistent')
    if not gate['full_acceptance'] and not any(marker in __version__ for marker in ['a', 'b', 'rc', 'dev']):
        raise CrawlerError('A final version cannot be packaged with incomplete acceptance')
    with ZipFile(data_zip, 'x', ZIP_DEFLATED) as archive:
        for path in sorted(export.iterdir()):
            if not path.is_file() or path.suffix not in {'.json', '.csv'}:
                raise CrawlerError('Unexpected data export file')
            archive.write(path, 'anhui-crawler-data/' + path.name)
    paths = [root / name for name in ['README.md', 'pyproject.toml', '.env.example', '.gitignore', '.gitattributes']]
    for folder in ['src', 'scripts', 'tests', 'docs', 'acceptance']:
        paths.extend(p for p in (root / folder).rglob('*') if p.is_file() and p.suffix != '.pyc' and
                     '__pycache__' not in p.parts and not any(part.endswith('.egg-info') for part in p.parts))
    paths += [wheel, release / f'dependencies-{__version__}.txt']
    paths += [release / original['name'] for original in baseline['old_release_files']]
    with ZipFile(source_zip, 'x', ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if path.is_symlink():
                raise CrawlerError('Source package symlinks refused')
            archive.write(path, 'anhui_public_data_crawler/' + path.relative_to(root).as_posix())
    private_receipt = None
    if args.private_seed:
        seed = args.private_seed.resolve()
        payload = json.loads(seed.read_text(encoding='utf-8'))
        target = root / 'data' / f'PRIVATE-acceptance-seed-{__version__}.zip'
        if target.exists():
            raise CrawlerError('Private seed package already exists')
        with ZipFile(target, 'x', ZIP_DEFLATED) as archive:
            archive.write(seed, 'private-seed/seed.json')
            written = set()
            for asset in payload['assets']:
                path = safe_path(seed.parent, asset['path'])
                raw = path.read_bytes()
                if sha256(raw) != asset['sha256'] or len(raw) != asset['byte_size']:
                    raise CrawlerError('Private seed integrity mismatch')
                if asset['path'] not in written:
                    archive.write(path, 'private-seed/' + asset['path'])
                    written.add(asset['path'])
        private_receipt = {**descriptor(target), 'private_originals_do_not_publish': True, 'asset_entries': len(payload['assets'])}
    for path in [source_zip, data_zip, wheel]:
        with ZipFile(path) as archive:
            if archive.testzip() or any('/.git/' in name or '/.venv/' in name or name.endswith(('.sqlite3', '/.env')) or '/raw/' in name for name in archive.namelist()):
                raise CrawlerError('Public package verification failed')
    result = {'format': 'anhui-crawler-release-2', 'version': __version__, 'packaged_at': now(),
        'published_to_remote': False, 'full_acceptance': gate['full_acceptance'], 'blockers': gate['blockers'],
        'source_tests': source_tests, 'installed_tests': installed_tests,
        'standard_facts': manifest['records'], 'candidate_facts': manifest['candidates'],
        'files': [descriptor(path) for path in [wheel, source_zip, data_zip]],
        'dependencies': descriptor(release / f'dependencies-{__version__}.txt'), 'private_seed': private_receipt,
        'old_releases_unchanged': len(baseline['old_release_files']), 'excluded_from_public': ['Original full text', 'Databases', 'Credentials', 'Runtime environments', 'Team packages']}
    write_json(receipt, result)
    print(json.dumps({'status': 'packaged_prerelease', 'version': __version__, 'full_acceptance': result['full_acceptance'], 'files': result['files']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
