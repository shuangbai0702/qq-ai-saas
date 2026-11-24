"""Seal verified FishBot control-plane backups, then publish from the operator PC."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import ssl
import struct
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b'FISHBOT-BACKUP-1\n'
COLD_MAGIC = b'FISHBOT-COLD-1\n'
LIMIT = 128 * 1024 * 1024
ARCHIVE = re.compile(r'fishbot-pg-\d{8}T\d{12}Z\.sealed')
FOLDER = 'application/vnd.google-apps.folder'
TAG = {'fishbotBackup': 'control-plane-v1'}
OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def bounded(path, maximum):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('Invalid backup input')
    with path.open('rb') as source:
        data = source.read(maximum + 1)
    if not data or len(data) > maximum:
        raise ValueError('Invalid backup input length')
    return data


def seal(directory, config, public_key, master_key, platform_key, keep):
    spec = importlib.util.spec_from_file_location('fishbot_pg_backup', Path(__file__).with_name('postgres-backup.py'))
    pg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pg)
    native = pg.NativePostgres(config)
    recipient = serialization.load_pem_public_key(bounded(public_key, 4096))
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve(strict=True)
    with pg.backup_lock(directory):
        dump_root = directory / 'postgres'
        manifest = pg.backup(dump_root, None, native.database, native.user, keep, True, native)
        stem = 'fishbot-pg-' + manifest['createdAt']
        dump = bounded(dump_root / (stem + '.dump'), LIMIT)
        if hashlib.sha256(dump).hexdigest() != manifest['sha256'] or not manifest['restoreCheck']['ok']:
            raise ValueError('Backup failed integrity check')
        files = {'postgres.dump': dump, 'manifest.json': json.dumps(manifest).encode(),
                 'master.key': bounded(master_key, 4096)}
        if platform_key and platform_key.is_file():
            files['platform-model-secret'] = bounded(platform_key, 16384)
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w') as archive:
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode = len(data), 0o600
                archive.addfile(entry, io.BytesIO(data))
        secret = AESGCM.generate_key(bit_length=256)
        wrapped = recipient.encrypt(secret, OAEP)
        nonce = os.urandom(12)
        header = MAGIC + struct.pack('!H', len(wrapped)) + wrapped + nonce
        final = pg.bounded_child(directory, stem + '.sealed')
        with tempfile.NamedTemporaryFile(dir=directory, prefix='.seal-', delete=False) as output:
            partial = Path(output.name)
            try:
                output.write(header)
                output.write(AESGCM(secret).encrypt(nonce, payload.getvalue(), header))
                output.flush()
                os.fsync(output.fileno())
            except BaseException:
                output.close()
                partial.unlink(missing_ok=True)
                raise
        try:
            partial.replace(final)
        finally:
            partial.unlink(missing_ok=True)
        for item in sorted(directory.glob('fishbot-pg-*.sealed'), reverse=True)[keep:]:
            if ARCHIVE.fullmatch(item.name) and not item.is_symlink() and item.is_file():
                item.unlink()
        print(json.dumps({'database': native.database, 'restoreCheck': manifest['restoreCheck'],
                          'sealed': final.name, 'bytes': final.stat().st_size}), flush=True)


def verify(file, key_path, restore_dir=None):
    data = bounded(file, LIMIT + 65536)
    if not data.startswith(MAGIC):
        raise ValueError('Unexpected backup format')
    start = len(MAGIC) + 2
    end = start + struct.unpack('!H', data[len(MAGIC):start])[0]
    nonce = data[end:end+12]
    key = serialization.load_pem_private_key(bounded(key_path, 8192), password=None)
    secret = key.decrypt(data[start:end], OAEP)
    payload = AESGCM(secret).decrypt(nonce, data[end+12:], data[:end+12])
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
        manifest = json.load(archive.extractfile('manifest.json'))
        if hashlib.file_digest(archive.extractfile('postgres.dump'), 'sha256').hexdigest() != manifest['sha256']:
            raise ValueError('Backup archive integrity check failed')
        if archive.getmember('master.key').size <= 0 or not manifest['restoreCheck']['ok']:
            raise ValueError('Missing recovery materials')
        if restore_dir:
            # Only fixed member names can be extracted. A new directory prevents overwrite.
            restore_dir.mkdir(mode=0o700)
            for name in ('postgres.dump', 'manifest.json', 'master.key', 'platform-model-secret'):
                try:
                    item = archive.getmember(name)
                except KeyError:
                    if name == 'platform-model-secret':
                        continue
                    raise
                if not item.isfile():
                    raise ValueError('Unexpected recovery archive member')
                with (restore_dir / name).open('xb') as output:
                    output.write(archive.extractfile(item).read())
    return manifest


def seal_cold(source, output, key_path):
    recipient = serialization.load_pem_public_key(bounded(key_path, 4096))
    secret = AESGCM.generate_key(bit_length=256)
    wrapped = recipient.encrypt(secret, OAEP)
    nonce = os.urandom(12)
    header = COLD_MAGIC + struct.pack('!H', len(wrapped)) + wrapped + nonce
    encrypted = AESGCM(secret).encrypt(nonce, bounded(source, LIMIT), header)
    with output.open('xb') as target:
        target.write(header)
        target.write(encrypted)


def open_cold(data, key_path):
    if not data.startswith(COLD_MAGIC):
        raise ValueError('Unexpected cold archive format')
    start = len(COLD_MAGIC) + 2
    end = start + struct.unpack('!H', data[len(COLD_MAGIC):start])[0]
    key = serialization.load_pem_private_key(bounded(key_path, 8192), password=None)
    secret = key.decrypt(data[start:end], OAEP)
    return AESGCM(secret).decrypt(data[end:end+12], data[end+12:], data[:end+12])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(url, headers=None, data=None, method=None):
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(urllib.request.Request(url, data=data, headers=headers or {}, method=method), timeout=120) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError('Google response exceeded size limit')
    return json.loads(body) if body else {}


class Drive:
    def __init__(self, oauth):
        credential = json.loads(bounded(oauth, 16384))
        data = {key: credential[key] for key in ('client_id', 'client_secret', 'refresh_token')}
        data['grant_type'] = 'refresh_token'
        token = request('https://oauth2.googleapis.com/token', data=urllib.parse.urlencode(data).encode())['access_token']
        self.headers = {'Authorization': 'Bearer ' + token}

    def call(self, path, params=None, body=None, method=None, content_type='application/json', upload=False):
        prefix = 'https://www.googleapis.com/' + ('upload/' if upload else '') + 'drive/v3/'
        return request(prefix + path + ('?' + urllib.parse.urlencode(params) if params else ''),
                       {**self.headers, 'Content-Type': content_type}, body, method)

    def list(self, query, fields, order='createdTime desc'):
        params = {'q': query + ' and trashed=false', 'fields': 'nextPageToken,files(' + fields + ')',
                  'pageSize': 100, 'orderBy': order}
        files = []
        for _ in range(10):
            result = self.call('files', params)
            files.extend(result.get('files', []))
            if not result.get('nextPageToken'):
                return files
            params['pageToken'] = result['nextPageToken']
        raise ValueError('Unexpected backup folder size')

    def upload(self, file, cold=False):
        folders = self.list("name='FishBot Encrypted Backups' and mimeType='" + FOLDER +
                            "' and 'root' in parents and appProperties has { key='fishbotBackup' and value='control-plane-v1' }", 'id')
        if len(folders) > 1:
            raise ValueError('Ambiguous Drive backup folder')
        folder = folders[0]['id'] if folders else self.call('files', {'fields': 'id'}, json.dumps(
            {'name': 'FishBot Encrypted Backups', 'mimeType': FOLDER, 'appProperties': TAG}).encode())['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', folder):
            raise ValueError('Unexpected Drive folder identity')
        tag = {'fishbotBackup': 'cold-storage-v1'} if cold else TAG
        existing = self.list("'" + folder + "' in parents and appProperties has { key='fishbotBackup' and value='" +
                             tag['fishbotBackup'] + "' }",
                             'id,name,md5Checksum,size,ownedByMe', 'name desc')
        data = bounded(file, LIMIT + 65536)
        digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
        verified = next((item for item in existing if item.get('ownedByMe') and item['name'] == file.name and
                         item.get('md5Checksum') == digest and int(item.get('size', 0)) == len(data)), None)
        if not verified:
            boundary = uuid.uuid4().hex
            meta = json.dumps({'name': file.name, 'parents': [folder], 'appProperties': tag}).encode()
            marker = ('--' + boundary + '\r\n').encode()
            body = (marker + b'Content-Type: application/json; charset=UTF-8\r\n\r\n' + meta + b'\r\n' +
                    marker + b'Content-Type: application/octet-stream\r\n\r\n' + data +
                    ('\r\n--' + boundary + '--\r\n').encode())
            verified = self.call('files', {'uploadType': 'multipart', 'fields': 'id,name,md5Checksum,size,ownedByMe'},
                                 body, 'POST', 'multipart/related; boundary=' + boundary, upload=True)
            if verified.get('md5Checksum') != digest or int(verified.get('size', 0)) != len(data):
                raise ValueError('Drive upload failed integrity check')
        if cold:
            identity = verified['id']
            if not verified.get('ownedByMe') or not re.fullmatch(r'[A-Za-z0-9_-]+', identity):
                raise ValueError('Unexpected cold archive owner or identity')
            url = 'https://www.googleapis.com/drive/v3/files/' + identity + '?alt=media'
            opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            with opener.open(urllib.request.Request(url, headers=self.headers), timeout=120) as response:
                downloaded = response.read(len(data) + 1)
            if downloaded != data:
                raise ValueError('Downloaded cold archive does not match uploaded bytes')
            return downloaded, identity
        owned = {item['id']: item for item in [*existing, verified]
                 if item.get('ownedByMe') and item.get('md5Checksum') and ARCHIVE.fullmatch(item['name'])}
        # Keep the seven newest verified archives. Only tagged files created for this backup are eligible.
        for item in sorted(owned.values(), key=lambda item: (item['name'], item['id']), reverse=True)[7:]:
            identity = item['id']
            if re.fullmatch(r'[A-Za-z0-9_-]+', identity):
                self.call('files/' + identity, body=b'{"trashed":true}', method='PATCH')
        print(json.dumps({'uploaded': file.name, 'bytes': len(data), 'driveChecksumVerified': True}), flush=True)


def operator_sync(remote, remote_dir, oauth, private):
    # ssh is used with a fixed host alias and absolute deployment path, so it preserves host-key validation.
    if not re.fullmatch(r'[A-Za-z0-9_-]+', remote) or not re.fullmatch(r'[A-Za-z]:/[A-Za-z0-9_/-]+', remote_dir):
        raise ValueError('Invalid SSH backup location')
    directory = remote_dir.replace('/', '\\')
    query = f"[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-ChildItem -LiteralPath '{directory}' -Filter '*.sealed' -File | Sort-Object Name -Descending | Select-Object -ExpandProperty Name"
    import base64
    encoded = base64.b64encode(query.encode('utf-16le')).decode()
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', remote, 'powershell',
                             '-NoProfile', '-EncodedCommand', encoded], check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=30, text=True, encoding='utf-8')
    names = [name for name in result.stdout.splitlines() if ARCHIVE.fullmatch(name)]
    if not names:
        raise ValueError('No completed encrypted archive on server')
    with tempfile.TemporaryDirectory(prefix='fishbot-encrypted-') as temp:
        file = Path(temp) / names[0]
        subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                        remote + ':' + remote_dir + '/' + file.name, str(file)], check=True, timeout=180)
        manifest = verify(file, private)
        Drive(oauth).upload(file)
        print(json.dumps({'database': manifest['database'], 'restoreCheck': manifest['restoreCheck']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    keys = commands.add_parser('keygen')
    keys.add_argument('private', type=Path)
    keys.add_argument('public', type=Path)
    remote = commands.add_parser('seal')
    for option in ('directory', 'config', 'public-key', 'master-key'):
        remote.add_argument('--' + option, type=Path, required=True)
    remote.add_argument('--platform-key', type=Path)
    remote.add_argument('--keep', type=int, default=2, choices=range(2, 8))
    sync = commands.add_parser('sync')
    sync.add_argument('remote')
    sync.add_argument('remote_dir')
    sync.add_argument('oauth', type=Path)
    sync.add_argument('private', type=Path)
    check = commands.add_parser('verify')
    check.add_argument('archive', type=Path)
    check.add_argument('private', type=Path)
    check.add_argument('--restore-directory', type=Path)
    cold = commands.add_parser('open-cold')
    cold.add_argument('archive', type=Path)
    cold.add_argument('private', type=Path)
    cold.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.mode == 'keygen':
        private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        with args.private.open('xb') as f:
            f.write(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
        with args.public.open('xb') as f:
            f.write(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo))
    elif args.mode == 'seal':
        seal(args.directory, args.config, args.public_key, args.master_key, args.platform_key, args.keep)
    elif args.mode == 'sync':
        operator_sync(args.remote, args.remote_dir, args.oauth, args.private)
    elif args.mode == 'open-cold':
        os.umask(0o077)
        payload = open_cold(bounded(args.archive, LIMIT + 65536), args.private)
        with args.output.open('xb') as f:
            f.write(payload)
        print(json.dumps({'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}), flush=True)
    else:
        os.umask(0o077)
        result = verify(args.archive, args.private, args.restore_directory)
        print(json.dumps({'database': result['database'], 'restoreCheck': result['restoreCheck']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        print('Backup request failed: HTTP ' + str(error.code), flush=True)
        raise SystemExit(1)
    except Exception as error:
        print('Backup failed: ' + type(error).__name__, flush=True)
        raise SystemExit(1)
