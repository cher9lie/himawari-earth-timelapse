"""One-command installation and configuration, using a project-local venv."""
import os
from pathlib import Path
import subprocess
import sys
import venv

root=Path(__file__).resolve().parent
os.chdir(root)
environment=root/'.venv'
python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
if not python.exists():venv.create(environment,with_pip=True)
subprocess.run([str(python),'-m','pip','install','-e',str(root)+'[colab]'],check=True)
subprocess.run([str(python),'-m','himawari_timelapse.cli','configure'],check=True)
choice=input('接下来：1=只保存配置，2=测量云端速度，3=开始云端制作 [1]: ').strip() or '1'
if choice in ['2','3']:
    command='benchmark' if choice=='2' else 'run'
    subprocess.run([str(python),'-m','himawari_timelapse.cli',command,'--backend','colab'],check=True)
