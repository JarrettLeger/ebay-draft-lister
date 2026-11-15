import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from . import services
from .models import Listing

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def natural(name):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', name)]


def sync_folder(folder, pairing, photos, connect, lock):
    source = Path(folder).expanduser()
    if not source.is_dir():
        raise ValueError('Source folder is unavailable. Connect the Z: drive or choose a reachable folder in Settings.')
    if source.resolve() == photos.resolve() or photos.resolve().is_relative_to(source.resolve()):
        raise ValueError('Choose a source folder outside the app data folder')
    paths = sorted((p for p in source.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS), key=lambda p: natural(p.name))
    result = dict(scanned=len(paths), imported=0, skipped=0, paired=0, errors=[])
    new_ids = set()
    originals = {}
    with lock, connect() as conn:
        for payload, in conn.execute('SELECT payload FROM cards'):
            old = json.loads(payload)
            for side, original in (('front', old['original']), ('back', old.get('back_original'))):
                if old.get(side) and original:
                    originals.setdefault(original, []).append((old[side], old['id']))
    for path in paths:
        destination = None
        try:
            before = path.stat()
            if before.st_size > 25 * 1024 * 1024:
                raise ValueError('Photo exceeds 25 MB')
            name = os.path.normcase(str(path.resolve()))
            checksum = digest(path)
            with connect() as conn:
                known = conn.execute('SELECT 1 FROM sync_sources WHERE source=? AND digest=?', (name, checksum)).fetchone()
            if known:
                result['skipped'] += 1
                continue
            photo = uuid4().hex + path.suffix.lower()
            destination = photos / photo
            shutil.copyfile(path, destination)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or digest(destination) != checksum:
                raise ValueError('Photo changed during sync; try again when copying finishes')
            services.jpeg(destination)
            card = dict(id=uuid4().hex, front=photo, back=None, original=path.name,
                front_url=None, back_url=None, listing=Listing().model_dump(mode='json'),
                state='imported', warnings=[], error=None, usage={})
            with lock, connect() as conn:
                # Recognize previously picker-imported photos instead of duplicating them.
                existing_photo = None
                existing_id = None
                for candidate, candidate_id in originals.get(path.name, []):
                    if digest(photos / candidate) == checksum:
                        existing_photo = candidate
                        existing_id = candidate_id
                        break
                if existing_photo:
                    conn.execute('INSERT INTO sync_sources VALUES (?,?,?)', (name, checksum, existing_photo))
                else:
                    conn.execute('INSERT INTO cards VALUES (?,?)', (card['id'], json.dumps(card)))
                    conn.execute('INSERT INTO sync_sources VALUES (?,?,?)', (name, checksum, photo))
            if existing_photo:
                destination.unlink()
                new_ids.add(existing_id)
                result['skipped'] += 1
            else:
                new_ids.add(card['id'])
                result['imported'] += 1
        except Exception as exc:
            if destination:
                destination.unlink(missing_ok=True)
            result['errors'].append({'file': path.name, 'error': str(exc) if isinstance(exc, ValueError) else 'Could not read/copy photo; check file and folder access'})
    if pairing != 'none':
        with lock, connect() as conn:
            synced = {row[0] for row in conn.execute('SELECT photo FROM sync_sources')}
            cards = [json.loads(row[0]) for row in conn.execute('SELECT payload FROM cards')]
            singles = sorted((c for c in cards if c['state'] == 'imported' and not c['back'] and c['front'] in synced), key=lambda c: natural(c['original']))
            named, leftovers, pairs = {}, [], []
            for card in singles:
                match = re.match(r'^(.*?)[_ -](front|back)$', Path(card['original']).stem, re.I)
                if match:
                    named.setdefault(match[1].casefold(), {}).setdefault(match[2].lower(), []).append(card)
                else:
                    leftovers.append(card)
            for group in named.values():
                if len(group.get('front', [])) == len(group.get('back', [])) == 1:
                    front, back = group['front'][0], group['back'][0]
                    if front['id'] in new_ids or back['id'] in new_ids:
                        pairs.append((front, back, 'matching filenames'))
            if pairing == 'consecutive':
                remaining = [c for c in leftovers if c['id'] in new_ids]
                pairs.extend((remaining[i], remaining[i+1], 'consecutive filenames') for i in range(0, len(remaining)-1, 2))
            for front, back, reason in pairs:
                front.update(back=back['front'], back_original=back['original'], state='needs_review')
                front['listing']['reviewed'] = False
                front['warnings'].append(f'Pair suggested from {reason}; verify both photos before export')
                conn.execute('UPDATE cards SET payload=? WHERE id=?', (json.dumps(front), front['id']))
                conn.execute('DELETE FROM cards WHERE id=?', (back['id'],))
                result['paired'] += 1
    return result
