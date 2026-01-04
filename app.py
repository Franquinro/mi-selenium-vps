import os
import time
import sqlite3
import pandas as pd
from datetime import datetime
from flask import Flask, render_template_string, send_file
from apscheduler.schedulers.background import BackgroundScheduler

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)

# --- CONFIGURACIÓN ---
USERNAME = os.getenv('SCRAP_USER', 'enelint%5CesUsuario')
PASSWORD1 = os.getenv('SCRAP_PASS1', 'Pass1')
PASSWORD2 = os.getenv('SCRAP_PASS2', 'Pass2')

URL_BASE = "https://eworkerbrrc.endesa.es/PIVision/#/Displays/88153/Balance-Combustible-Bco"
DB_NAME = "datos_temporales.db"
SCREENSHOT_PATH = "debug_screenshot.png"

DATOS_A_BUSCAR = (
    ('\\PI-BRRC-S1\\BRRC00-0LBL111A', 'BARRANCO - TANQUE ALMACEN FO'),
    ('\\PI-BRRC-S1\\BRRC00-0LBL111B', 'BARRANCO - TANQUE ALMACEN GO A'),
    ('\\PI-BRRC-S1\\BRRC00-0LBL111C', 'BARRANCO - TANQUE ALMACEN GO B'),
    ('\\PI-BRRC-S1\\BRRC036EGD20CL001JT01A', 'BARRANCO - TANQUE DIARIO GO 1'),
    ('\\PI-BRRC-S1\\BRRC036EGD20CL002JT01A', 'BARRANCO - TANQUE DIARIO GO 2'),
    ('\\PI-BRRC-S1\\BRRC036EGD20CL003JT01A', 'BARRANCO - TANQUE DIARIO GO 3'),
    ('\\PI-BRRC-S1\\BRRC0210EGB30CL001JT01A', 'BARRANCO - TANQUE DIARIO GO 4'),
    ('\\PI-BRRC-S1\\BRRC00-0LTBM127', 'BARRANCO - TANQUE GO VAPORES 80MW'),
    ('\\PI-JINA-S1\\JINA00-145J045822', 'JINAMAR - NIVEL TANQUE TO2A'),
    ('\\PI-JINA-S1\\JINAGT-208J021809', 'JINAMAR - NIVEL TQ GO 2 LM TURBINAS GAS'),
    ('\\PI-JINA-S1\\JINA00-145J045826', 'JINAMAR - NIVEL GO DIESEL 4/5')
)

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS lecturas (id INTEGER PRIMARY KEY AUTOINCREMENT, descripcion TEXT, valor TEXT, fecha TEXT)')
    conn.commit()
    conn.close()

def ejecutar_scrapping():
    print(f"[{datetime.now()}] Iniciando captura...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # User-Agent de escritorio real para evitar que PI Vision se ponga en modo "Tablet"
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    
    try:
        # 1. AUTENTICACIÓN (Ir a la raíz para establecer la sesión)
        # Limpiamos las credenciales por si acaso tienen caracteres especiales
        url_login = f"https://{USERNAME}:{PASSWORD1}@eworkerbrrc.endesa.es/PIVision/"
        print("Paso 1: Autenticando en la raíz...")
        driver.get(url_login)
        time.sleep(10) # Esperamos a que cargue la pantalla azul de "Recent" que vimos antes

        # 2. NAVEGACIÓN FORZADA POR JAVASCRIPT
        # En apps Angular, a veces es mejor cambiar el hash manualmente
        print(f"Paso 2: Forzando navegación al Display...")
        # Intentamos navegación normal primero
        driver.get(URL_BASE)
        time.sleep(5)
        
        # Si sigue en la Home, inyectamos JS para mover el router de Angular
        display_hash = "#/Displays/88153/Balance-Combustible-Bco"
        driver.execute_script(f"window.location.hash = '{display_hash}';")
        
        # 3. ESPERA DE CARGA DE DATOS
        print("Paso 3: Esperando renderizado de datos (30s)...")
        # PI Vision es pesado, 30 segundos es prudente para un VPS
        time.sleep(30) 
        
        # Tomamos captura para ver si ya estamos dentro del display
        driver.save_screenshot(SCREENSHOT_PATH)

        # 4. CAPTURA DE VALORES
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        fecha_actual = datetime.now().strftime("%H:%M:%S")

        encontrados = 0
        for tag, descripcion in DATOS_A_BUSCAR:
            try:
                # XPath mejorado: buscamos el div que tiene el tag en el title
                # Pero extraemos el texto de su descendiente directo o el valor total
                xpath = f"//div[contains(@title, '{tag}')]"
                
                # Usamos WebDriverWait para cada elemento por si acaso alguno tarda más
                elemento = WebDriverWait(driver, 10).until(
                    EC.visibility_of_element_located((By.XPATH, xpath))
                )
                
                # Truco para PI Vision: A veces el texto no es visible para .text 
                # pero sí para innerText vía JS
                valor = driver.execute_script("return arguments[0].innerText;", elemento).strip()
                
                # PI Vision a veces pone el valor en una línea nueva, lo limpiamos
                if "\n" in valor:
                    valor = valor.split("\n")[-1] # Intentamos pillar el último trozo (el número)

                if not valor or valor == "": 
                    valor = "Cargando..."

                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha) VALUES (?, ?, ?)", 
                               (descripcion, valor, fecha_actual))
                encontrados += 1
                print(f"[{encontrados}] {descripcion}: {valor}")
            except:
                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha) VALUES (?, ?, ?)", 
                               (descripcion, "No detectado", fecha_actual))
        
        conn.commit()
        conn.close()
        print(f"Proceso completado. Capturados {encontrados} de {len(DATOS_A_BUSCAR)}")

    except Exception as e:
        print(f"Error crítico: {e}")
        driver.save_screenshot(SCREENSHOT_PATH)
    finally:
        driver.quit()

# --- RUTAS WEB ---
@app.route('/')
def index():
    try:
        conn = sqlite3.connect(DB_NAME)
        df = pd.read_sql_query("SELECT descripcion, valor, fecha FROM lecturas ORDER BY id DESC LIMIT 11", conn)
        conn.close()
        data = df.values.tolist()
    except:
        data = []
    
    # Invertir para que salgan en orden normal si se prefiere
    data.reverse()

    html = """
    <html>
        <head>
            <title>Panel Niveles</title>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
            <meta http-equiv="refresh" content="30">
        </head>
        <body class="bg-light">
            <div class="container py-4">
                <div class="card shadow">
                    <div class="card-header bg-dark text-white d-flex justify-content-between">
                        <h4 class="mb-0">Niveles de Combustible</h4>
                        <small>Refresco cada 30s</small>
                    </div>
                    <div class="card-body">
                        <table class="table table-striped">
                            <thead><tr><th>Tanque</th><th>Valor</th><th>Hora</th></tr></thead>
                            <tbody>
                                {% for row in data %}
                                <tr>
                                    <td>{{ row[0] }}</td>
                                    <td><span class="badge bg-success fs-6">{{ row[1] }}</span></td>
                                    <td>{{ row[2] }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                        {% if not data %}
                        <div class="alert alert-warning text-center">Iniciando sistema... espera 30 segundos.</div>
                        {% endif %}
                    </div>
                    <div class="card-footer text-center">
                        <a href="/debug" class="btn btn-sm btn-outline-secondary">Ver Diagnóstico (Screenshot)</a>
                    </div>
                </div>
            </div>
        </body>
    </html>
    """
    return render_template_string(html, data=data)

@app.route('/debug')
def debug():
    """Ruta para ver qué está viendo el navegador en el VPS."""
    if os.path.exists(SCREENSHOT_PATH):
        return send_file(SCREENSHOT_PATH, mimetype='image/png')
    return "No hay captura disponible todavía.", 404

if __name__ == "__main__":
    init_db()
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=ejecutar_scrapping, trigger="interval", hours=1)
    scheduler.start()
    
    # Ejecución inicial
    ejecutar_scrapping()
    
    app.run(host='0.0.0.0', port=5000)


