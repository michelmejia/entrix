"""Browser integration: real UI, disposable DB and mock camera frames."""
from contextlib import closing
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_app, init_db
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server


def main():
    root = Path(__file__).resolve().parents[1]
    artifacts = root / 'test-results'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(Path(directory) / 'test.db')
        init_db(app)
        server = make_server('127.0.0.1', 0, app, threaded=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.get_by_label('Código de instalación').fill(app.config['SETUP_CODE'])
                page.get_by_label('Tu nombre', exact=True).fill('Administrador prueba')
                page.get_by_label('Usuario', exact=True).fill('admin')
                page.get_by_label('Contraseña', exact=True).fill('Prueba-segura-2026')
                page.get_by_role('button', name='Crear administrador').click()
                expect(page.get_by_role('heading', name='Iniciar turno')).to_be_visible()
                page.get_by_label('Usuario', exact=True).fill('admin')
                page.get_by_label('Contraseña', exact=True).fill('Prueba-segura-2026')
                page.get_by_role('button', name='Iniciar sesión', exact=True).click()
                expect(page.get_by_role('heading', name='Venta rápida')).to_be_visible()
                page.get_by_role('button', name='Eventos y tarifas', exact=True).click()
                form = page.locator('#eventForm')
                form.get_by_label('Nombre', exact=True).fill('Noche de prueba')
                form.get_by_label('Fecha', exact=True).fill('2026-10-10')
                form.get_by_label('Hora', exact=True).fill('20:00')
                form.get_by_label('Lugar', exact=True).fill('Salón principal')
                form.get_by_label('Aforo máximo').fill('50')
                form.get_by_label('Moneda del evento').select_option('CLP')
                form.get_by_label('Nombre de tarifa').fill('General')
                form.get_by_label('Precio de tarifa').fill('10000')
                form.get_by_role('button', name='Crear evento', exact=True).click()
                expect(page.get_by_role('heading', name='Noche de prueba')).to_be_visible()
                page.get_by_role('button', name='Venta rápida', exact=True).click()
                page.locator('#qty').fill('3')
                page.locator('#tender').fill('50000')
                expect(page.locator('#change')).to_contain_text('20.000')
                page.screenshot(path=str(artifacts / 'venta-escritorio.png'), full_page=True)
                page.get_by_role('button', name='Cobrar y entregar entradas', exact=True).click()
                expect(page.locator('dialog .ticket')).to_have_count(3)
                expect(page.locator('dialog .error')).to_have_text('')
                tokens = page.locator('dialog .qr').evaluate_all('(els)=>els.map(e=>e.dataset.token)')
                with page.expect_download() as download:
                    # Three images are emitted, capture the first to verify image generation.
                    page.get_by_role('button', name='Descargar imagen', exact=True).click()
                download.value.save_as(str(artifacts / 'ticket.png'))
                decoded = page.evaluate('''async bytes => {
                    const host=document.createElement('div');host.id='fileDecode';document.body.appendChild(host);
                    const decoder=new Html5Qrcode('fileDecode');
                    try {return await decoder.scanFile(new File([new Uint8Array(bytes)],'ticket.png',{type:'image/png'}),false);}
                    finally {decoder.clear();host.remove();}
                }''', list((artifacts / 'ticket.png').read_bytes()))
                assert decoded == tokens[0], 'Downloaded ticket QR could not be decoded'
                page.get_by_role('button', name='Continuar atendiendo', exact=True).click()
                observer = context.new_page()
                observer.goto(url)
                observer.get_by_role('button', name='Entradas', exact=True).click()
                ticket_id = page.evaluate('state.lastSale.tickets[0].id')
                observer.locator('#query').fill(ticket_id)
                observer.get_by_role('button', name='Buscar', exact=True).click()
                expect(observer.locator('#ticketList tbody tr')).to_have_count(1)
                expect(observer.locator('#ticketList .badge')).to_have_text('VENDIDA')
                page.get_by_role('button', name='Validar ingreso', exact=True).click()
                page.locator('#scanToken').fill(tokens[0])
                page.locator('#scanToken').press('Enter')
                expect(page.locator('#scanResult h2')).to_have_text('Puede pasar')
                expect(observer.locator('#ticketList .badge')).to_have_text('USADA', timeout=7000)
                expect(observer.locator('#query')).to_have_value(ticket_id)
                expect(observer.locator('#ticketList tbody tr')).to_have_count(1)
                expect(observer.locator('#ticketList tbody')).to_contain_text('Administrador prueba')
                expect(observer.locator('#sync')).to_contain_text('cada 2 s')
                observer.route('**/api/tickets?*', lambda route: route.abort())
                expect(observer.locator('#sync')).to_contain_text('reintentando', timeout=7000)
                expect(observer.locator('#ticketList .badge')).to_have_text('USADA')
                observer.unroute('**/api/tickets?*')
                expect(observer.locator('#sync')).to_contain_text('cada 2 s', timeout=7000)
                observer.close()
                page.locator('#scanToken').fill(tokens[0])
                page.locator('#scanToken').press('Enter')
                expect(page.locator('#scanResult h2')).to_have_text('Ya utilizada')
                # Camera callback integration: native detector and real MediaStream from canvas.
                page.evaluate('''() => {
                    window.fakeToken = '';
                    window.BarcodeDetector = class {
                        static async getSupportedFormats(){return ['qr_code'];}
                        async detect(){return window.fakeToken ? [{rawValue:window.fakeToken}] : [];}
                    };
                    navigator.mediaDevices.getUserMedia = async () => {
                        const canvas=document.createElement('canvas');canvas.width=300;canvas.height=300;
                        const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,300,300);
                        window.mockStream=canvas.captureStream(10);return window.mockStream;
                    };
                }''')
                page.get_by_role('button', name='Abrir cámara', exact=True).click()
                expect(page.locator('#cameraStop')).to_be_visible()
                page.evaluate('(token)=>window.fakeToken=token', tokens[1])
                expect(page.locator('#scanResult h2')).to_have_text('Puede pasar')
                page.wait_for_timeout(2200)
                expect(page.locator('#scanResult h2')).to_have_text('Puede pasar')
                page.evaluate('(token)=>window.fakeToken=token', tokens[2])
                expect(page.locator('#scanCount')).to_contain_text('3 ingresos')
                expect(page.locator('#camera')).to_be_visible()
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(artifacts / 'acceso-movil.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile horizontal overflow'
                page.get_by_role('button', name='Venta rápida', exact=True).click()
                page.wait_for_function("window.mockStream.getTracks().every(t=>t.readyState==='ended')")
                # Commit the sale on the server but drop the HTTP response to the browser.
                calls = []
                def lose_response(route):
                    if not calls:
                        response = route.fetch()
                        assert response.status == 200
                        calls.append(True)
                        route.abort('failed')
                    else:
                        route.continue_()
                page.route('**/api/sales/quick', lose_response)
                page.get_by_role('button', name='Cobrar y entregar entradas', exact=True).click()
                expect(page.locator('#pending')).to_be_visible()
                expect(page.locator('#saleForm .error')).to_contain_text('Sin respuesta')
                page.get_by_role('button', name='Comprobar / reintentar', exact=True).click()
                expect(page.locator('dialog .ticket')).to_have_count(1)
                page.get_by_role('button', name='Continuar atendiendo', exact=True).click()
                page.unroute('**/api/sales/quick', lose_response)
                page.screenshot(path=str(artifacts / 'venta-movil.png'), full_page=True)
                with closing(sqlite3.connect(app.config['DATABASE'])) as c:
                    assert c.execute('SELECT count(*) FROM payments').fetchone()[0] == 4, 'Retry duplicated payment'
                page.get_by_role('button', name='Cobrar y dar ingreso', exact=True).click()
                expect(page.locator('dialog h2')).to_have_text('Ingreso registrado')
                expect(page.locator('dialog .ticket-state')).to_have_text('INGRESO YA REGISTRADO')
                page.get_by_role('button', name='Continuar atendiendo', exact=True).click()
                page.get_by_role('button', name='Caja', exact=True).click()
                expect(page.locator('.stat b')).to_contain_text('50.000')
                page.get_by_role('button', name='Personal', exact=True).click()
                page.get_by_label('Nombre', exact=True).fill('Puerta uno')
                page.get_by_label('Usuario', exact=True).fill('puerta')
                page.get_by_label('Contraseña inicial').fill('Puerta-segura-2026')
                page.get_by_label('Rol', exact=True).select_option('validador')
                page.get_by_role('button', name='Crear cuenta', exact=True).click()
                expect(page.get_by_text('Puerta uno', exact=True)).to_be_visible()
                page.get_by_role('button', name='Cerrar sesión', exact=True).click()
                expect(page.get_by_role('heading', name='Iniciar turno')).to_be_visible()
                page.get_by_label('Usuario', exact=True).fill('puerta')
                page.get_by_label('Contraseña', exact=True).fill('Puerta-segura-2026')
                page.get_by_role('button', name='Iniciar sesión', exact=True).click()
                expect(page.get_by_role('heading', name='Validar ingreso')).to_be_visible()
                expect(page.locator('nav button')).to_have_count(1)

                page.get_by_role('button', name='Cerrar sesión', exact=True).click()
                page.get_by_label('Usuario', exact=True).fill('admin')
                page.get_by_label('Contraseña', exact=True).fill('Prueba-segura-2026')
                page.get_by_role('button', name='Iniciar sesión', exact=True).click()
                page.get_by_role('button', name='Configuración', exact=True).click()
                page.locator('#resetPrepare select').select_option('full')
                page.locator('#resetPrepare input').fill('Prueba-segura-2026')
                page.get_by_role('button', name='Preparar respaldo', exact=True).click()
                expect(page.locator('#resetFinal')).to_be_disabled()
                with page.expect_download() as reset_download:
                    page.get_by_role('button', name='Descargar respaldo', exact=True).click()
                reset_path = Path(directory)/'downloaded-reset.db'
                reset_download.value.save_as(str(reset_path))
                with closing(sqlite3.connect(reset_path)) as c:
                    assert c.execute('SELECT count(*) FROM users').fetchone()[0] == 2
                page.locator('#resetExecute input[name=password]').fill('Prueba-segura-2026')
                page.locator('#resetExecute input[name=confirmation]').fill('RESTABLECER')
                page.locator('#resetFinal').click()
                expect(page.get_by_role('heading', name='Prepara tu equipo')).to_be_visible()
                expect(page.locator('#auth input[name=setupCode]')).not_to_be_empty()
                page.get_by_label('Tu nombre', exact=True).fill('Nuevo administrador')
                page.get_by_label('Usuario', exact=True).fill('nuevo')
                page.get_by_label('Contraseña', exact=True).fill('Nueva-clave-2026')
                page.get_by_role('button', name='Crear administrador', exact=True).click()
                expect(page.get_by_role('heading', name='Iniciar turno')).to_be_visible()
                assert not errors, errors
                context.close()
                browser.close()
                print('UI OK: setup, login, event, batch sale, images, manual and continuous scanning, mobile, lost-response retry, immediate admission, cash and validator role.')
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()


if __name__ == '__main__':
    main()
