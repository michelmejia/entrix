"""Shutdown integration using a disposable project and dummy processes."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

def main():
    with tempfile.TemporaryDirectory(prefix='acceso shutdown ') as directory:
        root = Path(directory)
        shutil.copyfile(Path(__file__).resolve().parents[1]/'finalizar.ps1', root/'finalizar.ps1')
        (root/'server.py').write_text('import time; time.sleep(120)')
        owned = subprocess.Popen([sys.executable, str(root/'server.py')], creationflags=subprocess.CREATE_NO_WINDOW)
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            time.sleep(0.5)
            command = ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'finalizar.ps1')]
            subprocess.run(command+['-WhatIf'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            assert owned.poll() is None, 'Simulation stopped a process'
            subprocess.run(command,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            owned.wait(timeout=8)
            assert unrelated.poll() is None, 'Unrelated Python was stopped'
            assert (root/'server.py').is_file(), 'Project file removed'
            subprocess.run(command,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            print('Shutdown OK: scoped process termination, simulation, repeat execution, unrelated Python preserved.')
        finally:
            for process in (owned,unrelated):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)

if __name__ == '__main__':
    main()
