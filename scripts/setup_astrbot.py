from pathlib import Path
import shutil, subprocess, json, sys
root=Path(__file__).resolve().parents[1]
source=root.parent/'qq-ai-saas-research/sources/AstrBotDevs__AstrBot'
target=root/'references/astrbot'
if not target.exists():
    if source.exists():
        target.parent.mkdir(exist_ok=True)
        shutil.copytree(source,target,ignore=shutil.ignore_patterns('.git','.venv','__pycache__','node_modules'))
        sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
    else:
        subprocess.run(['git','clone','https://github.com/AstrBotDevs/AstrBot',str(target)],check=True)
        sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=target,text=True).strip()
    (target.parent/'astrbot-version.json').write_text(json.dumps({'repository':'https://github.com/AstrBotDevs/AstrBot','commit':sha},indent=2),encoding='utf8')
raise SystemExit(subprocess.call(['uv','sync','--no-dev','--frozen'],cwd=target))
