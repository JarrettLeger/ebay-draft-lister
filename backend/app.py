import csv
import io
import json
import os
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import services
from .compat import HEADER_ROW, INFO_ROWS, build_title
from .models import Listing, issues

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


def create_app(data_dir=None):
    root = Path(data_dir or os.getenv('LISTER_DATA_DIR', ROOT / 'data')).resolve()
    root.mkdir(parents=True, exist_ok=True)
    photos = root / 'photos'
    photos.mkdir(exist_ok=True)
    db = root / 'inventory.sqlite3'
    lock = threading.Lock()

    def connect():
        connection = sqlite3.connect(db, timeout=30)
        connection.execute('PRAGMA journal_mode=WAL')
        return connection

    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        # Network work interrupted by shutdown must be recoverable.
        for ident, payload in conn.execute('SELECT id,payload FROM cards').fetchall():
            card = json.loads(payload)
            if card['state'] == 'processing':
                card.update(state='failed', error='Interrupted; retry the operation')
                conn.execute('UPDATE cards SET payload=? WHERE id=?', (json.dumps(card), ident))

    def read(ident):
        with connect() as conn:
            row = conn.execute('SELECT payload FROM cards WHERE id=?', (ident,)).fetchone()
        if not row:
            raise HTTPException(404, 'Card not found')
        return json.loads(row[0])

    def save(card):
        with connect() as conn:
            conn.execute('INSERT OR REPLACE INTO cards VALUES (?,?)', (card['id'], json.dumps(card)))

    def decorate(card):
        card['issues'] = issues(card)
        return card

    app = FastAPI(title='eBay Draft Lister v2')

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        host = request.headers.get('host', '').split(':')[0]
        origin = request.headers.get('origin')
        if host not in ('127.0.0.1', 'localhost', 'testserver'):
            return Response('Invalid host', 403)
        if origin and origin not in ('http://127.0.0.1:8765', 'http://localhost:8765'):
            return Response('Invalid origin', 403)
        return await call_next(request)

    @app.get('/api/config')
    def config():
        return {'openai': bool(os.getenv('OPENAI_API_KEY')), 'model': os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
                'cloudinary': bool(os.getenv('CLOUDINARY_CLOUD_NAME') and os.getenv('CLOUDINARY_UPLOAD_PRESET'))}

    @app.get('/api/cards')
    def cards():
        with connect() as conn:
            return [decorate(json.loads(row[0])) for row in conn.execute('SELECT payload FROM cards ORDER BY rowid DESC')]

    @app.post('/api/import')
    def import_photos(files: list[UploadFile] = File(...)):
        if not 1 <= len(files) <= 100:
            raise HTTPException(400, 'Import 1–100 photos at a time')
        imported = []
        try:
            for file in files:
                if Path(file.filename or '').suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'):
                    raise HTTPException(400, 'Unsupported photo format')
                content = file.file.read(25 * 1024 * 1024 + 1)
                if len(content) > 25 * 1024 * 1024:
                    raise HTTPException(400, 'Each photo must be at most 25 MB')
                name = uuid4().hex + Path(file.filename).suffix.lower()
                path = photos / name
                path.write_bytes(content)
                imported.append((name, file.filename))
                services.jpeg(path)  # Decode before adding anything to inventory.
        except Exception as exc:
            for name, _ in imported:
                (photos / name).unlink(missing_ok=True)
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(400, 'Cannot decode photo; check format or HEIC support') from exc
        # Every image starts independently. Pairing is explicit, so a missing back cannot shift the batch.
        result = []
        with lock:
            for name, original in imported:
                card = dict(id=uuid4().hex, front=name, back=None, original=original,
                    front_url=None, back_url=None, listing=Listing().model_dump(mode='json'),
                    state='imported', warnings=[], error=None, usage={})
                save(card)
                result.append(decorate(card))
        return result

    @app.get('/api/photos/{name}')
    def photo(name: str):
        if Path(name).name != name or not (photos / name).is_file():
            raise HTTPException(404)
        return Response(services.jpeg(photos / name, vision=True), media_type='image/jpeg')

    @app.put('/api/cards/{ident}')
    def edit(ident: str, listing: Listing):
        with lock:
            card = read(ident)
            if card['state'] == 'processing':
                raise HTTPException(409, 'Processing; wait before editing')
            card['listing'] = listing.model_dump(mode='json')
            card['state'] = 'ready' if not issues(card) else 'needs_review'
            save(card)
            return decorate(card)

    class Pair(BaseModel):
        back_id: str

    @app.post('/api/cards/{ident}/pair')
    def pair(ident: str, body: Pair):
        with lock:
            card, back = read(ident), read(body.back_id)
            if ident == body.back_id or card['back'] or back['back'] or any(c['state'] != 'imported' for c in (card, back)):
                raise HTTPException(409, 'Pair two unprocessed single photos')
            card.update(back=back['front'], state='needs_review')
            with connect() as conn:
                conn.execute('UPDATE cards SET payload=? WHERE id=?', (json.dumps(card), ident))
                conn.execute('DELETE FROM cards WHERE id=?', (body.back_id,))
            return decorate(card)

    @app.post('/api/cards/{ident}/unpair')
    def unpair(ident: str):
        with lock:
            card = read(ident)
            if not card['back'] or card['state'] == 'processing':
                raise HTTPException(409, 'No removable back photo')
            back = dict(id=uuid4().hex, front=card['back'], back=None, original='Separated back photo',
                front_url=None, back_url=None, listing=Listing().model_dump(mode='json'), state='imported', warnings=[], error=None, usage={})
            card.update(back=None, front_url=None, back_url=None, state='imported', warnings=[])
            card['listing']['reviewed'] = False
            with connect() as conn:
                for item in (card, back):
                    conn.execute('INSERT OR REPLACE INTO cards VALUES (?,?)', (item['id'], json.dumps(item)))
            return decorate(card)

    @app.post('/api/cards/{ident}/{operation}')
    def process(ident: str, operation: str):
        if operation not in ('extract', 'upload', 'swap'):
            raise HTTPException(404)
        with lock:
            card = read(ident)
            if card['state'] == 'processing':
                raise HTTPException(409, 'Already processing')
            if operation == 'swap':
                if not card['back']:
                    raise HTTPException(400, 'No back photo')
                card['front'], card['back'] = card['back'], card['front']
                card['front_url'], card['back_url'] = card['back_url'], card['front_url']
                card['listing']['reviewed'] = False
                card['state'] = 'needs_review'
                save(card)
                return decorate(card)
            card.update(state='processing', error=None)
            if operation == 'extract':
                card['listing']['reviewed'] = False
            save(card)
        try:
            if operation == 'extract':
                fields, usage = services.extract([photos / card[k] for k in ('front', 'back') if card[k]])
                card['usage'] = usage
                card['warnings'] = fields.pop('warnings')
                if fields.pop('images_match') is False:
                    card['warnings'].append('Front/back may not match; inspect pairing')
                card['listing'].update(fields)
                card['listing'].update(title=build_title(fields), reviewed=False,
                    description=f"{fields['player']} — {fields['set_name']}. See photos for condition. Please review all details.")
            else:
                for side in ('front', 'back'):
                    if card[side] and not card[side + '_url']:
                        card[side + '_url'] = services.upload(photos / card[side])
                        save(card)  # Retain successful front upload when back fails.
            card['state'] = 'ready' if not issues(card) else 'needs_review'
        except Exception:
            card.update(state='failed', error='Operation failed. Check configuration/network and retry. Saved fields and photos were retained.')
        with lock:
            save(card)
        return decorate(card)

    class Export(BaseModel):
        ids: list[str]

    @app.post('/api/export')
    def export(body: Export):
        with lock:
            selected = [read(ident) for ident in dict.fromkeys(body.ids)]
            if not selected or any(issues(c) or c['state'] == 'processing' for c in selected):
                raise HTTPException(400, 'Select reviewed, priced cards with uploaded photos')
            stream = io.StringIO(newline='')
            stream.write('\r\n'.join(INFO_ROWS + [HEADER_ROW]) + '\r\n')
            writer = csv.writer(stream, lineterminator='\r\n')
            def safe(value):
                value = str(value)
                return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
            for card in selected:
                item = card['listing']
                writer.writerow(['Draft', card['id'], item['category_id'], safe(item['title']), '',
                    f"{float(item['price']):.2f}", '1', '|'.join(card[s + '_url'] for s in ('front', 'back') if card[s + '_url']),
                    item['condition_id'], safe(item['description']), 'FixedPrice'])
            for card in selected:
                card['state'] = 'exported'
                save(card)
        return Response(stream.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="eBay_Drafts.csv"'})

    dist = ROOT / 'frontend' / 'dist'
    if dist.exists():
        app.mount('/', StaticFiles(directory=dist, html=True), name='ui')
    return app


app = create_app()
