from pathlib import Path
import shutil,subprocess,json
root=Path(__file__).resolve().parents[1];src=root.parent/'qq-ai-saas-research/sources/HarcicYang__EulerOneBot';dst=root/'references/euler'
if not dst.exists():
    if src.exists():
        shutil.copytree(src,dst,ignore=shutil.ignore_patterns('.git','.venv','__pycache__'))
        sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=src,text=True).strip()
    else:
        subprocess.run(['git','clone','https://github.com/HarcicYang/EulerOneBot',str(dst)],check=True)
        sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=dst,text=True).strip()
    (dst.parent/'euler-version.json').write_text(json.dumps({'repository':'https://github.com/HarcicYang/EulerOneBot','commit':sha},indent=2),encoding='utf8')
raise SystemExit(subprocess.call(['uv','sync','--no-dev','--frozen'],cwd=dst))
