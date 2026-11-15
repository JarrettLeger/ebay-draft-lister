import csv
import io
import json
import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import services
from .compat import HEADER_ROW, INFO_ROWS, build_title
from .models import Listing, issues
from .settings import Settings
from .folder_sync import sync_folder
from .archive import archive_card
from .descriptions import build_description, description_html

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


def create_app(data_dir=None):
    root = Path(data_dir or os.getenv('LISTER_DATA_DIR', ROOT / 'data')).resolve()
    root.mkdir(parents=True, exist_ok=True)
    photos = root / 'photos'
    photos.mkdir(exist_ok=True)
    db = root / 'inventory.sqlite3'
    lock = threading.RLock()
    stopping = threading.Event()
    worker = None
    settings = Settings(root)
    sync_lock = threading.Lock()

    def connect():
        connection = sqlite3.connect(db, timeout=30)
        connection.execute('PRAGMA journal_mode=WAL')
        return connection

    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS sync_sources (source TEXT, digest TEXT, photo TEXT, PRIMARY KEY(source,digest))')
        conn.execute('CREATE TABLE IF NOT EXISTS photo_archives (photo TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        for ident, payload in conn.execute('SELECT id,payload FROM jobs').fetchall():
            job = json.loads(payload)
            if job['state'] in ('running', 'cancelling'):
                job.update(state='interrupted', current=None)
                conn.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(job), ident))
        # Network work interrupted by shutdown must be recoverable.
        for ident, payload in conn.execute('SELECT id,payload FROM cards').fetchall():
            card = json.loads(payload)
            if card['state'] == 'processing':
                card.update(state='failed', error='Interrupted; retry the operation')
            elif card['state'] == 'queued':
                card['state'] = card.pop('before_queue', 'needs_review')
            card.pop('job_id', None)
            if card != json.loads(payload):
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

    @asynccontextmanager
    async def lifespan(app):
        yield
        stopping.set()  # In-flight calls finish; no subsequent card starts.

    app = FastAPI(title='eBay Draft Lister v2', lifespan=lifespan)
    app.state.stop_background = stopping.set

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Validation failures must not echo submitted credentials.
        return JSONResponse(status_code=422, content={'detail': [
            {key: error[key] for key in ('loc', 'msg', 'type')} for error in exc.errors()]})

    def unavailable(card):
        return card['state'] in ('processing', 'queued')

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        host = request.headers.get('host', '').split(':')[0]
        origin = request.headers.get('origin')
        if host not in ('127.0.0.1', 'localhost', 'testserver'):
            return Response('Invalid host', 403)
        if origin and origin != f"http://{request.headers.get('host', '')}":
            return Response('Invalid origin', 403)
        return await call_next(request)

    @app.get('/api/config')
    def config():
        return settings.public()

    class Preferences(BaseModel):
        source_folder: str | None = Field(default=None, min_length=1, max_length=2048)
        model: str | None = Field(default=None, min_length=1, max_length=120)
        provider: Literal['openai', 'claude'] | None = None
        claude_model: str | None = Field(default=None, min_length=1, max_length=120)
        api_key: str | None = Field(default=None, max_length=1024)
        claude_api_key: str | None = Field(default=None, max_length=1024)
        cloud_name: str | None = Field(default=None, max_length=120)
        upload_preset: str | None = Field(default=None, max_length=250)
        clear_api_key: bool = False
        clear_claude_api_key: bool = False
        clear_cloudinary: bool = False

        @field_validator('source_folder', 'model', 'claude_model')
        @classmethod
        def nonempty(cls, value):
            if value is not None and not value.strip():
                raise ValueError('Enter a nonempty value')
            return value.strip() if value else value

    @app.put('/api/config')
    def update_config(body: Preferences):
        try:
            return settings.update(body.model_dump())
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc

    class Sync(BaseModel):
        pairing: Literal['consecutive', 'named', 'none'] = 'consecutive'

    @app.post('/api/sync')
    def sync(body: Sync):
        if not sync_lock.acquire(blocking=False):
            raise HTTPException(409, 'Folder sync is already running')
        try:
            return sync_folder(settings.public()['source_folder'], body.pairing, photos, connect, lock)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else 'Cannot access the source folder') from exc
        finally:
            sync_lock.release()

    @app.get('/api/cards')
    def cards():
        with connect() as conn:
            return [decorate(json.loads(row[0])) for row in conn.execute('SELECT payload FROM cards ORDER BY rowid DESC')]

    @app.post('/api/description')
    def generate_description(listing: Listing):
        # Preview only: never save or clear a card's manual edits implicitly.
        return {'description': build_description(listing.model_dump())}

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
    def photo(name: str, full: bool = False):
        if Path(name).name != name or not (photos / name).is_file():
            raise HTTPException(404)
        return Response(services.jpeg(photos / name, vision=not full), media_type='image/jpeg')

    @app.put('/api/cards/{ident}')
    def edit(ident: str, listing: Listing):
        with lock:
            card = read(ident)
            if unavailable(card):
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
            card.update(back=back['front'], back_original=back['original'], state='needs_review')
            with connect() as conn:
                conn.execute('UPDATE cards SET payload=? WHERE id=?', (json.dumps(card), ident))
                conn.execute('DELETE FROM cards WHERE id=?', (body.back_id,))
            return decorate(card)

    @app.post('/api/cards/{ident}/unpair')
    def unpair(ident: str):
        with lock:
            card = read(ident)
            if not card['back'] or unavailable(card):
                raise HTTPException(409, 'No removable back photo')
            back = dict(id=uuid4().hex, front=card['back'], back=None, original=card.get('back_original', 'Separated back photo'),
                front_url=None, back_url=None, listing=Listing().model_dump(mode='json'), state='imported', warnings=[], error=None, usage={})
            card.update(back=None, front_url=None, back_url=None, state='imported', warnings=[])
            card['listing']['reviewed'] = False
            with connect() as conn:
                for item in (card, back):
                    conn.execute('INSERT OR REPLACE INTO cards VALUES (?,?)', (item['id'], json.dumps(item)))
            return decorate(card)

    @app.post('/api/cards/{ident}/retry-archive')
    def retry_archive(ident: str):
        if not sync_lock.acquire(blocking=False):
            raise HTTPException(409, 'Wait for folder sync to finish before retrying archive')
        try:
            with lock:
                card = read(ident)
                export_id = card.get('last_export_id', '')
                if card['state'] != 'exported' or not export_id or not (root / 'exports' / f'{export_id}.csv').is_file():
                    raise HTTPException(409, 'Only an exported card with a saved CSV can retry its photo archive')
                archive_card(card, connect)
                save(card)
                return decorate(card)
        finally:
            sync_lock.release()

    @app.post('/api/cards/{ident}/{operation}')
    def process(ident: str, operation: str):
        return process_card(ident, operation)

    def process_card(ident, operation, job_id=None, connection_values=None):
        if operation not in ('extract', 'upload', 'swap'):
            raise HTTPException(404)
        with lock:
            card = read(ident)
            if card['state'] == 'processing' or (card.get('job_id') and card['job_id'] != job_id):
                raise HTTPException(409, 'Already processing')
            if operation == 'swap':
                if not card['back']:
                    raise HTTPException(400, 'No back photo')
                card['front'], card['back'] = card['back'], card['front']
                card['original'], card['back_original'] = card.get('back_original', 'Back photo'), card['original']
                card['front_url'], card['back_url'] = card['back_url'], card['front_url']
                card['listing']['reviewed'] = False
                card['state'] = 'needs_review'
                save(card)
                return decorate(card)
            card.pop('before_queue', None)
            card.update(state='processing', error=None)
            if operation == 'extract':
                card['listing']['reviewed'] = False
            save(card)
        try:
            if operation == 'extract':
                chosen = connection_values or settings.runtime()
                with services.configuration(chosen):
                    fields, usage = services.extract([photos / card[k] for k in ('front', 'back') if card[k]])
                card['usage'] = usage
                card['extraction_provider'] = chosen['LISTER_AI_PROVIDER']
                card['extraction_model'] = chosen['ANTHROPIC_MODEL'] if chosen['LISTER_AI_PROVIDER'] == 'claude' else chosen['OPENAI_MODEL']
                card['warnings'] = fields.pop('warnings')
                if fields.pop('images_match') is False:
                    card['warnings'].append('Front/back may not match; inspect pairing')
                card['listing'].update(fields)
                card['listing'].update(title=build_title(fields), reviewed=False,
                    description=build_description(fields))
            else:
                for side in ('front', 'back'):
                    if card[side] and not card[side + '_url']:
                        with services.configuration(settings.runtime()):
                            card[side + '_url'] = services.upload(photos / card[side])
                        save(card)  # Retain successful front upload when back fails.
            card['state'] = 'ready' if not issues(card) else 'needs_review'
        except Exception as exc:
            card.update(state='failed', error=services.safe_error(exc))
        with lock:
            save(card)
        return decorate(card)

    def read_job(ident):
        with connect() as conn:
            row = conn.execute('SELECT payload FROM jobs WHERE id=?', (ident,)).fetchone()
        if not row:
            raise HTTPException(404, 'Batch not found')
        return json.loads(row[0])

    def save_job(job):
        with connect() as conn:
            conn.execute('INSERT OR REPLACE INTO jobs VALUES (?,?)', (job['id'], json.dumps(job)))

    def run_batch(ident, connection_values):
        try:
            for card_id in read_job(ident)['ids']:
                with lock:
                    job = read_job(ident)
                    if job['state'] == 'cancelling' or stopping.is_set():
                        break
                    job['current'] = card_id
                    save_job(job)
                result = process_card(card_id, job['operation'], ident, connection_values)
                with lock:
                    job = read_job(ident)
                    finished = read(card_id)
                    finished.pop('job_id', None)
                    save(finished)
                    job['results'].append({'id': card_id, 'state': result['state']})
                    job['completed'] += 1
                    job['failed'] += int(result['state'] == 'failed')
                    save_job(job)
        except Exception:
            with lock:
                job = read_job(ident)
                job['state'] = 'interrupted'
                save_job(job)
        finally:
            with lock:
                job = read_job(ident)
                if job['state'] != 'interrupted':
                    job['state'] = ('cancelled' if job['state'] == 'cancelling' else
                                    'interrupted' if stopping.is_set() else 'completed')
                job['current'] = None
                # Release every reservation and restore unstarted cards atomically.
                with connect() as conn:
                    for card_id in job['ids']:
                        card = read(card_id)
                        if card.get('job_id') == ident:
                            card.pop('job_id', None)
                            if card['state'] == 'queued':
                                card['state'] = card.pop('before_queue')
                            elif card['state'] == 'processing':
                                card.update(state='failed', error='Interrupted; retry the operation')
                            conn.execute('UPDATE cards SET payload=? WHERE id=?', (json.dumps(card), card_id))
                    conn.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(job), ident))

    class Batch(BaseModel):
        ids: list[str] = Field(min_length=1)
        operation: Literal['extract', 'upload']

    @app.get('/api/jobs')
    def jobs():
        with connect() as conn:
            return [json.loads(row[0]) for row in conn.execute('SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 20')]

    @app.post('/api/jobs')
    def start_batch(body: Batch):
        nonlocal worker
        with lock:
            if stopping.is_set() or (worker and worker.is_alive()):
                raise HTTPException(409, 'A batch is still running; wait for it to finish')
            selected = [read(ident) for ident in dict.fromkeys(body.ids)]
            if any(unavailable(card) for card in selected):
                raise HTTPException(409, 'One or more cards are already queued or processing')
            connections = settings.runtime()
            provider = connections['LISTER_AI_PROVIDER']
            required = (('ANTHROPIC_API_KEY' if provider == 'claude' else 'OPENAI_API_KEY'),) if body.operation == 'extract' else ('CLOUDINARY_CLOUD_NAME', 'CLOUDINARY_UPLOAD_PRESET')
            if any(not connections.get(key) for key in required):
                raise HTTPException(400, 'Configure the connection in Settings before starting this batch')
            job = dict(id=uuid4().hex, operation=body.operation, ids=[c['id'] for c in selected],
                state='running', completed=0, failed=0, current=None, results=[],
                created_at=datetime.now(timezone.utc).isoformat())
            if body.operation == 'extract':
                job.update(provider=provider, model=connections['ANTHROPIC_MODEL'] if provider == 'claude' else connections['OPENAI_MODEL'])
            with connect() as conn:
                for card in selected:
                    card.update(before_queue=card['state'], state='queued', job_id=job['id'])
                    conn.execute('UPDATE cards SET payload=? WHERE id=?', (json.dumps(card), card['id']))
                conn.execute('INSERT INTO jobs VALUES (?,?)', (job['id'], json.dumps(job)))
            worker = threading.Thread(target=run_batch, args=(job['id'], connections), daemon=True)
            worker.start()
            return job

    @app.post('/api/jobs/{ident}/cancel')
    def cancel_batch(ident: str):
        with lock:
            job = read_job(ident)
            if job['state'] == 'running':
                job['state'] = 'cancelling'
                save_job(job)
            return job

    class Export(BaseModel):
        ids: list[str]

    @app.post('/api/export')
    def export(body: Export):
        # Do not let sync reimport a file in between export and its archive move.
        if not sync_lock.acquire(blocking=False):
            raise HTTPException(409, 'Wait for folder sync to finish before exporting')
        try:
            return export_cards(body)
        finally:
            sync_lock.release()

    def export_cards(body):
        with lock:
            selected = [read(ident) for ident in dict.fromkeys(body.ids)]
            if not selected or any(issues(c) or unavailable(c) for c in selected):
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
                    item['condition_id'], description_html(item['description']), 'FixedPrice'])
            export_id = uuid4().hex
            export_dir = root / 'exports'
            export_dir.mkdir(exist_ok=True)
            saved_csv = export_dir / f'{export_id}.csv'
            # Persist the complete CSV before any original photo is renamed or moved.
            with saved_csv.open('x', encoding='utf-8', newline='') as output:
                output.write(stream.getvalue())
                output.flush()
                os.fsync(output.fileno())
            archive_warning_count = 0
            for card in selected:
                card['state'] = 'exported'
                card['last_export_id'] = export_id
                save(card)
                archive_warning_count += len(archive_card(card, connect))
                save(card)
        return Response(stream.getvalue(), media_type='text/csv', headers={
            'Content-Disposition': 'attachment; filename="eBay_Drafts.csv"',
            'X-Export-ID': export_id, 'X-Archive-Warnings': str(archive_warning_count)})

    @app.get('/api/exports/{export_id}')
    def download_export(export_id: str):
        if len(export_id) != 32 or any(c not in '0123456789abcdef' for c in export_id):
            raise HTTPException(404)
        path = root / 'exports' / f'{export_id}.csv'
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type='text/csv', filename=f'eBay_Drafts_{export_id[:8]}.csv')

    dist = ROOT / 'frontend' / 'dist'
    if dist.exists():
        app.mount('/', StaticFiles(directory=dist, html=True), name='ui')
    return app


app = create_app()
