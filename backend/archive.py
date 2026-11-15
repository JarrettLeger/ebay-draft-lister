"""Journal source-photo renames; inventory copies and saved CSVs remain available."""
import json
import os
import re
from pathlib import Path

from .folder_sync import digest


def slug(value):
    return re.sub(r'[^\w-]+', '_', str(value), flags=re.UNICODE).strip('_')[:90] or 'Card'


def move_no_replace(source, destination):
    if os.name == 'nt':
        # Windows rename is atomic on this volume and fails if destination exists.
        os.rename(source, destination)
    else:
        os.link(source, destination)  # Exclusive destination creation, never overwrite.
        source.unlink()


def archive_card(card, connect):
    warnings = []
    archived = {}
    for side in ('front', 'back'):
        photo = card.get(side)
        if not photo:
            continue
        try:
            with connect() as conn:
                row = conn.execute('SELECT payload FROM photo_archives WHERE photo=?', (photo,)).fetchone()
                sources = conn.execute('SELECT source,digest FROM sync_sources WHERE photo=?', (photo,)).fetchall()
            plan = json.loads(row[0]) if row else None
            if plan and plan['state'] == 'moved':
                archived[side] = plan['destination']
                continue
            if not plan:
                if len(sources) != 1:
                    raise ValueError('Source location unknown or ambiguous; sync the source folder before retrying export')
                source_name, checksum = sources[0]
                source = Path(source_name)
                done = source.parent / 'Done'
                base = slug('_'.join(str(card['listing'].get(k) or '') for k in ('player', 'year', 'set_name')))
                filename = f'{base}_{card["id"][:8]}_{side}{source.suffix}'
                target = done / filename
                index = 2
                while target.exists():
                    target = done / f'{Path(filename).stem}_{index}{source.suffix}'
                    index += 1
                plan = dict(source=str(source), digest=checksum, destination=str(target), state='planned')
                with connect() as conn:
                    conn.execute('INSERT INTO photo_archives VALUES (?,?)', (photo, json.dumps(plan)))
            source, target = Path(plan['source']), Path(plan['destination'])
            expected = source.parent.resolve() / 'Done'
            if target.parent.resolve() != expected or source.resolve() == target.resolve():
                raise ValueError('Archive target must be inside the source folder’s Done directory')
            if not source.exists():
                if not target.is_file() or digest(target) != plan['digest']:
                    raise ValueError('Original photo is missing; no verified archived copy was found')
                # A crash after rename but before saving the completed journal is recoverable.
            else:
                if digest(source) != plan['digest']:
                    raise ValueError('Original photo changed since import; left untouched')
                if target.exists():
                    raise ValueError('Archive filename is now occupied; left both files untouched')
                target.parent.mkdir(exist_ok=True)
                move_no_replace(source, target)
            plan['state'] = 'moved'
            with connect() as conn:
                conn.execute('UPDATE photo_archives SET payload=? WHERE photo=?', (json.dumps(plan), photo))
            archived[side] = str(target)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) else 'Could not move photo; check source drive, permissions or open files'
            warnings.append(f'{side}: {reason}')
    card['archived_photos'] = archived
    card['archive_warnings'] = warnings
    return warnings
