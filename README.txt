ENTRIX V9.1 – Lotes y Preventa

Cambio: las entradas JPG/ZIP de los lotes ahora muestran claramente el precio de la entrada según la tarifa configurada.

﻿ENTRIX V9 - LOTES, NUMERACION, VENTA Y CONTROL DE INGRESO

INICIAR
1. Ejecuta INICIAR.bat. Se requiere Python 3.11 o superior. El iniciador detecta automaticamente la instalacion de Python y recrea .venv si fue creada en otra PC.
2. En el primer inicio, copia el codigo de instalacion que aparece en la consola.
3. En el navegador, crea tu administrador con usuario y clave de al menos 10 caracteres.
4. Inicia sesion. En Personal crea cuentas individuales para vendedores y validadores.
5. En Eventos y tarifas configura el aforo, fecha, lugar y precios.
6. En Entradas puedes crear lotes de preventa. Cada evento usa un correlativo único: 0001, 0002, 0003...
   El lote fija la tarifa y genera un ZIP con JPG individuales listos para compartir por WhatsApp.
7. Durante la preventa puedes anotar solo el número de entrada (por ejemplo 0027). Luego usa Entradas → Registrar venta por número para completar cliente y pago.
8. El QR contiene un identificador interno seguro distinto del número visible. El número visible nunca cambia.
   Los eventos antiguos conservan entradas y pagos; agrega sus tarifas antes de vender.

ATENDER LA COLA
- Vendedor: elige evento una vez, tarifa, cantidad y medio de pago.
- Cobrar y entregar entradas: registra el pago total y entrega un QR por persona.
- Cobrar y dar ingreso: registra venta, pago e ingreso en la misma transaccion.
  Usalo solamente donde el vendedor controla el paso fisico de las personas.
- Nombre y telefono son opcionales. El efectivo recibido permite calcular el vuelto.
- El cobro por transferencia o tarjeta se confirma manualmente tras verificarlo.
  No hay conexion automatica con bancos ni terminales de pago.
- Entradas: permite buscar por número, nombre, teléfono o ID; vender entradas emitidas con pago parcial y cobrar saldos.
- Lotes: genera entradas consecutivas por evento, fija la tarifa del lote y permite descargar todas como JPG dentro de un ZIP.
- Cada JPG muestra ENTRIX, evento, QR y el número de entrada. El QR no depende del número visible.
- Una entrada individual también puede descargarse como JPG desde la API autenticada.
- Validador: selecciona evento y puerta, abre camara una vez o usa lector USB con Enter.
  Verde permite pasar; rojo bloquea; amarillo indica enviar a caja.
  El lector permanece abierto. Retira cada QR antes de mostrar el siguiente.
- Si falta respuesta de red, no cobres otra vez: usa Comprobar / reintentar.
  Se conserva la misma operacion en la pestana incluso al recargar.

ENTREGA
- Lotes de preventa: descarga un ZIP con JPG individuales nombrados ENTRIX-0001.jpg, ENTRIX-0002.jpg, etc.
- Impresion/PDF: QR generado localmente. Formato de ticket de 72 mm para papel de 80 mm.
  La seleccion de impresora y el corte dependen del controlador del equipo.
- Imagen PNG individual y compartir mediante el menu del dispositivo.
- WhatsApp automatico y la impresion silenciosa requieren integraciones adicionales.
- No se envian tokens a servicios externos para crear el QR.

SEGURIDAD Y DATOS
- Sesiones de hasta 12 horas, cookies HttpOnly, contrasenas con hash y roles en servidor.
- Desactivar una cuenta revoca sus sesiones. La clave antigua 1234 ya no sirve.
- API autenticada y proteccion CSRF. Solo static/ es publica; tickets.db no se publica.
- Operaciones de cobro y validacion con identificador para reintentos sin duplicacion.
- Los importes operativos se guardan en centavos enteros.
- Los tickets tienen ticket_number (correlativo visible por evento), tariff_id y batch_id; los datos antiguos se migran automáticamente.
  Los decimales anteriores se conservan, al igual que tokens y fechas de ingreso.
- Migracion automatica con respaldo previo en backups/ al detectar la version anterior.
- Los medios de pago antiguos se marcan sin_clasificar para no inventar su origen.
- Caja calcula totales de todo el historial y separa efectivo, tarjeta y transferencia.
- Exportaciones CSV completas de ventas y caja; columnas *_cents expresadas en centavos.
- Cancelar una entrada no devuelve dinero. Una devolucion se registra como retiro
  indicando el numero de entrada y el motivo; la entrada debe cancelarse por separado.

RESPALDOS
Ejecuta RESPALDAR.bat al cierre de la jornada y guarda una copia fuera de esta PC.
Usa la API de respaldo de SQLite y verifica la integridad del archivo resultante.
Para recuperar datos: detiene el servidor; conserva una copia de tickets.db y sus
archivos tickets.db-wal/tickets.db-shm si existen; mueve esos archivos originales
fuera de la carpeta activa; copia el respaldo verificado como tickets.db y reinicia.
No mezcles un respaldo con archivos WAL/SHM de otra base.
Mantener una base SQLite activa dentro de OneDrive puede causar conflictos de
sincronizacion. Ejecuta una sola instancia del servidor sobre una copia local;
los otros dispositivos deben conectarse a ella por navegador.

CELULARES Y DESPLIEGUE
- PC: http://127.0.0.1:5000
- Misma red: http://IP-DE-LA-PC:5000 (lector USB/manual; camara movil necesita HTTPS).
- Publica HTTPS mediante tu infraestructura antes de usar camaras en celulares.
- Cuando todo el acceso sea HTTPS, define ACCESO_HTTPS=1 para cookies Secure.
- El inicio usa Waitress. ACCESO_HOST permite fijar la interfaz; PORT cambia el puerto.
- Las bibliotecas QR estan en static/vendor y no requieren Internet para cargarse.
- Se necesita conexion con el servidor para vender o validar; no hay admision offline.

PRUEBAS (datos temporales, no modifica la base real)
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.venv\Scripts\python.exe -B tests/smoke_ui.py
La camara de las pruebas se simula. Verifica camara fisica, sonido e impresora
con los equipos que se usaran antes de atender publico.

ARCHIVOS
server.py: API y migracion. static/: interfaz y bibliotecas QR locales.
legacy/: copia del servidor y de la interfaz anterior, no servida por HTTP.
build.py: crea ZIP de distribucion sin bases de datos ni sesiones.

ACCESO HTTPS DESDE EL CELULAR
Ejecuta CONECTAR_CELULAR.bat en esta PC. La primera vez descarga cloudflared
oficial y comprueba SHA-256 antes de ejecutarlo. Si ya hay un servidor local,
lo utiliza. Si no, inicia el servidor en segundo plano.
Se requiere haber creado el administrador antes de publicar el enlace.
Aparecera ENLACE_CELULAR.html con un QR y el enlace HTTPS: abre ese enlace en
Chrome/Safari en tu celular, inicia sesion y permite la camara.
Ambos dispositivos necesitan Internet; no hace falta que compartan Wi-Fi.
Puedes cerrar la ventana del iniciador. Manten la PC encendida y conectada.
DESCONECTAR_CELULAR.bat detiene solo el tunel de este proyecto, no el servidor.
El enlace de trycloudflare es temporal y cambia cuando se crea un tunel nuevo.
Es una opcion de prueba: Cloudflare no garantiza disponibilidad y limita los
Quick Tunnels a 200 solicitudes simultaneas. Para un evento real configura un
tunel permanente con cuenta Cloudflare y dominio propio.
No se instalan servicios de Windows ni se abren puertos del router.
Los registros de conexion se guardan en .conexion/ y no se publican en la web.
Documentacion: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/


RESTABLECER Y FINALIZAR
- Administrador: Configuracion > Restablecer sistema. Puedes conservar usuarios o volver a crear el primer administrador. Requiere descargar respaldo, contrasena y escribir RESTABLECER.
- Se guarda otra copia verificada en backups justo antes del borrado. Todos los QR anteriores quedan invalidados y se cierran las sesiones.
- FINALIZAR.bat cierra el servidor, auxiliares Python del proyecto y el tunel del celular. Conserva la base y los respaldos. No cierra el navegador ni otros programas. Puedes cerrar la ventana de INICIAR cuando termine.
- Para volver a trabajar: INICIAR.bat; para un nuevo enlace HTTPS: CONECTAR_CELULAR.bat.


CAMBIO V9.3: al registrar la venta de una entrada perteneciente a un lote, el pago inicial se precarga automáticamente con el precio de la tarifa del lote. Así, una entrada prepagada queda lista para validación el día del evento; el monto sigue siendo editable para registrar un abono si corresponde.
