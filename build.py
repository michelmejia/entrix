"""Empaqueta únicamente código; excluye datos, sesiones y entornos locales."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from datetime import datetime
root = Path(__file__).resolve().parent
output = root / ('Acceso-V8-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip')
files = [root / name for name in ('server.py','database.py','data_checks.py','postgres_schema.sql','backup.py','abrir_navegador.py','INICIAR.bat','RESPALDAR.bat','README.txt','requirements.txt','requirements-render.txt')]
files += list((root / 'static').rglob('*'))
with ZipFile(output, 'x', ZIP_DEFLATED) as archive:
    for file in files:
        if file.is_file():
            archive.write(file, file.relative_to(root))
print(output)
