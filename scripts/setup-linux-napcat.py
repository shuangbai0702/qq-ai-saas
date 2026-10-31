"""Build-time only: verify a pinned official shell, never download latest on boot."""
import hashlib,json,os,sys,tempfile,time,urllib.request,zipfile
from pathlib import Path,PurePosixPath

def extract(archive,destination):
    with zipfile.ZipFile(archive) as source:
        entries=source.infolist()
        if len(entries)>20000 or sum(i.file_size for i in entries)>300*1024*1024:
            raise ValueError('Unexpected NapCat archive size')
        main=[i for i in entries if PurePosixPath(i.filename).name=='napcat.mjs']
        if len(main)!=1:raise ValueError('Exactly one NapCat shell is required')
        prefix=PurePosixPath(main[0].filename).parent
        for item in entries:
            name=PurePosixPath(item.filename)
            if name.is_absolute() or '\\' in item.filename or '..' in name.parts or (item.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Unsafe archive entry')
            if prefix!=PurePosixPath('.') and not name.is_relative_to(prefix):continue
            relative=name.relative_to(prefix)
            if not relative.parts:continue
            target=destination.joinpath(*relative.parts)
            if item.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with source.open(item) as incoming,target.open('xb') as output:
                    while chunk:=incoming.read(1024*1024):output.write(chunk)
        if not (destination/'napcat.mjs').is_file():raise ValueError('Missing NapCat shell')

def main():
    from napcat_login_patch import patch_login
    config=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));destination=Path(sys.argv[2])
    if destination.exists():raise ValueError('Refusing to replace an existing runtime')
    with tempfile.TemporaryDirectory(prefix='napcat-release-') as temporary:
        archive=Path(temporary)/'shell.zip';digest=hashlib.sha256();size=0;started=time.monotonic()
        # An operator may cache/mirror the public asset at build time. The pinned
        # official SHA-256 remains mandatory regardless of its download location.
        request=urllib.request.Request(os.environ.get('NAPCAT_BUNDLE_URL') or config['bundleUrl'],headers={'User-Agent':'Fishbot-image-build'})
        with urllib.request.urlopen(request,timeout=30) as response,archive.open('xb') as output:
            while chunk:=response.read(1024*1024):
                size+=len(chunk)
                if size>64*1024*1024 or time.monotonic()-started>600:raise ValueError('NapCat download limit exceeded')
                digest.update(chunk);output.write(chunk)
        if digest.hexdigest()!=config['bundleSha256']:raise ValueError('Official NapCat release checksum mismatch')
        extract(archive,destination)
        patch_login(destination/'napcat.mjs')
    print('Verified official NapCat shell '+config['napcatVersion'])

if __name__=='__main__':main()
