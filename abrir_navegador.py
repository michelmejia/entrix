"""Abre el navegador cuando el servidor responde, sin ventana auxiliar."""
import time
import urllib.request
import webbrowser
for _ in range(60):
    try:
        with urllib.request.urlopen('http://127.0.0.1:5000/api/auth/status', timeout=1) as response:
            if response.status == 200:
                webbrowser.open('http://127.0.0.1:5000')
                break
    except OSError:
        time.sleep(1)
