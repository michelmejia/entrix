# Entrix en Render y Neon

El uso local continúa con `tickets.db`. `DATABASE_URL` selecciona PostgreSQL.
En Render, el servidor exige PostgreSQL para no guardar ventas en un disco efímero.

## Preparación y pruebas

En PowerShell, dentro del proyecto:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-render.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

Las pruebas PostgreSQL se omiten si falta `ENTRIX_TEST_DATABASE_URL`. Para
ejecutarlas, crea una base de prueba independiente en Neon. Introduce su URL
directa, con pooling desactivado, sin guardarla en el historial:

```powershell
$env:ENTRIX_TEST_DATABASE_URL = [System.Net.NetworkCredential]::new('', (Read-Host 'URL de la base de PRUEBA' -AsSecureString)).Password
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_postgres.py -v
```

Las pruebas crean y eliminan sus propios esquemas con nombres aleatorios.
No uses la URL de producción para pruebas. La suite comprueba concurrencia,
aforo, doble validación, reintentos, caja, respaldo y migración de ida y vuelta.

## Migración de datos existentes

Usa una base Neon dedicada y vacía. No crees todavía el administrador en Render.
Los usuarios, hashes de contraseña, entradas, QR, pagos e historial se copian
sin generar nuevos identificadores. La base SQLite original no se modifica.

```powershell
.\.venv\Scripts\python.exe migrate_neon.py
```

El programa pide la URL de Neon de forma oculta. También admite la variable
de sesión `ENTRIX_NEON_URL`. La ejecución predeterminada crea un respaldo local
y revisa el destino sin escribir en Neon. No pegues la URL en un chat ni en Git.

Cuando las pruebas pasen y la revisión sea correcta, detén el servidor local
y todas las ventas/validaciones. Con el servidor detenido:

```powershell
.\.venv\Scripts\python.exe migrate_neon.py --apply --source-stopped
```

La herramienta crea un respaldo nuevo, copia en una transacción y verifica
todos los campos de todas las filas antes de confirmar. Un fallo revierte la
transacción. Si el destino ya contiene datos, se detiene sin sobrescribirlos.
No vuelvas a vender en la copia SQLite después de la migración definitiva.
Si la conexión se interrumpe durante COMMIT, verifica el destino antes de
reintentar: podría haberse confirmado; la protección de destino no vacío
impide duplicar registros.

## Servicio web en Render

Conecta el repositorio `michelmejia/entrix` desde tu cuenta de Render.

- Runtime: Python.
- Build command: `pip install -r requirements-render.txt`.
- Start command: `python server.py`.
- Health check path: `/healthz`.
- `DATABASE_URL`: URL de la base Neon que recibió los datos (con SSL).
- `ACCESO_HTTPS`: `1`.
- `ACCESO_SETUP_CODE`: un secreto aleatorio, para cualquier instalación vacía futura.

El servidor escucha en `0.0.0.0` y respeta `PORT`. Ejecuta una sola instancia
Waitress: la preparación de restablecimientos conserva tokens en memoria.
Los bloqueos transaccionales de PostgreSQL serializan las operaciones de
escritura entre conexiones y conservan las garantías del servidor SQLite.

Comprueba con un usuario migrado que eventos, entradas y caja coincidan antes
de abrir ventas. Conserva los respaldos fuera de GitHub.

## Respaldos y restablecimiento

El restablecimiento en PostgreSQL genera un SQLite restaurable y verificado,
incluido un respaldo final dentro de la transacción que borra los datos.
En Render se bloquea esta función hasta configurar `ACCESO_BACKUP_DIR` en un
disco persistente montado en el servicio. No basta con indicar una carpeta
del disco efímero. La disponibilidad y el costo del disco dependen del plan.
El inicio y las ventas no requieren dicho disco cuando usan Neon.

Para exportar manualmente PostgreSQL, usa `backup.backup_postgres` con una
conexión de `database.connect`; para SQLite, `python backup.py` sigue vigente.
Un respaldo descargado puede contener hashes de contraseñas,
datos de clientes y tokens de entradas: consérvalo de forma privada.
