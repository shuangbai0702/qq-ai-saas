"""Download the official portable Windows/Node runtime; never touches an installed QQ."""
from pathlib import Path
import hashlib,json,subprocess,zipfile,sys
ROOT=Path(__file__).resolve().parents[1]
VERSION='v4.18.41'
URL=f'https://github.com/NapNeko/NapCatQQ/releases/download/{VERSION}/NapCat.Shell.Windows.Node.zip'
SHA256='51fbb10e7bcd56b0c2a6de86c37811e7518330e48a137ac689ab1e4d8627d3f0'
def install():
    if sys.platform!='win32':raise SystemExit('This pinned package is Windows x64 only; Linux packaging is not validated.')
    downloads=ROOT/'references/napcat-download';downloads.mkdir(parents=True,exist_ok=True)
    archive=downloads/'NapCat.Shell.Windows.Node.zip'
    def digest():
        h=hashlib.sha256()
        with archive.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        return h.hexdigest()
    if not archive.exists():subprocess.run(['curl.exe','--fail','--location','--connect-timeout','20','--max-time','600',URL,'--output',str(archive)],check=True)
    if digest()!=SHA256:raise SystemExit('Archive SHA256 mismatch; do not run or overwrite an unverified runtime')
    target=ROOT/'references/napcat-windows-node'
    if not target.exists():
        target.mkdir()
        with zipfile.ZipFile(archive) as zipfile_:
            for item in zipfile_.infolist():
                dest=(target/item.filename).resolve()
                if not dest.is_relative_to(target.resolve()):raise RuntimeError('Unsafe archive path')
            zipfile_.extractall(target)
    for name in ['node.exe','wrapper.node','napcat/napcat.mjs','config.json','package.json']:
        if not (target/name).is_file():raise SystemExit('Runtime incomplete: '+name)
    (target/'fishbot-upstream.json').write_text(json.dumps({'repository':'https://github.com/NapNeko/NapCatQQ','release':VERSION,'url':URL,'sha256':SHA256,'scope':'official portable runtime; separate from the desktop QQ install'},indent=2),encoding='utf8')
    print(target)
if __name__=='__main__':install()
