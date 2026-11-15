"""Local preferences; Windows protects saved credentials with the current user's DPAPI key."""
import base64
import ctypes
import json
import os
from pathlib import Path
from threading import RLock

DEFAULT_FOLDER = r'Z:\Match Day Cards\eBay'
FIELDS = ('OPENAI_API_KEY', 'OPENAI_MODEL', 'CLOUDINARY_CLOUD_NAME', 'CLOUDINARY_UPLOAD_PRESET')


def protect(value, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('Saving credentials requires Windows; use environment variables on other systems')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    payload = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise RuntimeError('Windows could not protect or unlock the saved connection settings')
    try:
        result = ctypes.string_at(output.data, output.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


class Settings:
    def __init__(self, root: Path):
        self.path = root / 'settings.json'
        self.lock = RLock()
        self.values = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}

    def runtime(self):
        with self.lock:
            result = {key: os.getenv(key, '') for key in FIELDS}
            for key, value in self.values.get('connections', {}).items():
                result[key] = protect(value, decrypt=True) if value else ''
            result['OPENAI_MODEL'] = self.values.get('model', result['OPENAI_MODEL'] or 'gpt-4.1-mini')
            return result

    def public(self):
        config = self.runtime()
        return dict(openai=bool(config['OPENAI_API_KEY']), model=config['OPENAI_MODEL'],
            cloudinary=bool(config['CLOUDINARY_CLOUD_NAME'] and config['CLOUDINARY_UPLOAD_PRESET']),
            cloud_name=config['CLOUDINARY_CLOUD_NAME'],
            source_folder=self.values.get('source_folder', DEFAULT_FOLDER))

    def update(self, changes):
        with self.lock:
            values = json.loads(json.dumps(self.values))
            for key in ('source_folder', 'model'):
                if changes.get(key) is not None:
                    values[key] = changes[key].strip()
            connections = values.setdefault('connections', {})
            for name, key in (('api_key', 'OPENAI_API_KEY'), ('cloud_name', 'CLOUDINARY_CLOUD_NAME'), ('upload_preset', 'CLOUDINARY_UPLOAD_PRESET')):
                value = changes.get(name)
                if value is not None and value.strip():
                    connections[key] = protect(value.strip())
            if changes.get('clear_api_key'):
                connections['OPENAI_API_KEY'] = ''
            if changes.get('clear_cloudinary'):
                connections['CLOUDINARY_CLOUD_NAME'] = connections['CLOUDINARY_UPLOAD_PRESET'] = ''
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(values), encoding='utf-8')
            temporary.replace(self.path)
            self.values = values
            return self.public()
