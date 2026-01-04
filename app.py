import os
import time
import base64
import sqlite3
import pandas as pd
from datetime import datetime
from flask import Flask, render_template_string, send_file
from apscheduler.schedulers.background import BackgroundScheduler

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)

# =============================================================================
# CONFIGURACIÓN Y VARIABLES DE ENTORNO
# =============================================================================
USERNAME = os.getenv("SCRAP_USER", r"enelint\usuario")
PASSWORD1 = os.getenv("SCRAP_PASS1", "pass1")
PASSWORD2 = os.getenv("SCRAP_PASS2", "")

DB_NAME = "temp_niveles.db"
SCREENSHOT_PATH = "debug_vps.png"
WINDOW_W, WINDOW_H = 1920, 1080

PI_BASE_URL = "https://eworkerbrrc.endesa.es/PIVision/"
DISPLAY_ID = "88153"
DISPLAY_SLUG = "Balance-Combustible-Bco"
KIOSK_QUERY = "mode=kiosk&hidetoolbar&redirect=false"

TARGET_HASHES = [
    f"#/Displays/{DISPLAY_ID}/{DISPLAY_SLUG}?{KIOSK_QUERY}",
    f"#/Displays/{DISPLAY_ID}?{KIOSK_QUERY}",
]

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

# =============================================================================
# LÓGICA DE NAVEGACIÓN AVANZADA (TU VERSIÓN LOCAL)
# =============================================================================

def set_basic_auth_header(driver, user, password):
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {
        "headers": {"Authorization": f"Basic {token}"}
    })

def inject_anti_automation(driver):
    script = r"""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
    Object.defineProperty(navigator, 'language', {get: () => 'es-ES'});
    """
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script})

def configure_desktop_mode(driver):
    version = (driver.capabilities.get("browserVersion") or "120").strip()
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"
    
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": ua, "platform": "Windows"})
    driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
        "mobile": False, "width": WINDOW_W, "height": WINDOW_H, "deviceScaleFactor": 1
    })

def build_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"--window-size={WINDOW_W},{WINDOW_H}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    
    driver = webdriver.Chrome(options=options)
    inject_anti_automation(driver)
    configure_desktop_mode(driver)
    return driver

def is_on_display(driver):
    return f"/#/Displays/{DISPLAY_ID}" in (driver.current_url or "")

def navigate_to_display(driver):
    for _ in range(3):
        driver.get(PI_BASE_URL + TARGET_HASHES[0])
        time.sleep(5)
        if is_on_display(driver): return True
        # Forzado por JS si cae en Recent
        for h in TARGET_HASHES:
            try:
                driver.execute_script("window.location.hash = arguments[0];", h)
                WebDriverWait(driver, 10).until(lambda d: is_on_display(d))
                return True
            except: continue
    return False

# =============================================================================
# TAREA DE SCRAPPING
# =============================================================================

def ejecutar_scrapping():
    print(f"[{datetime.now()}] Iniciando captura...")
    driver = build_driver()
    
    try:
        # Autenticación
        set_basic_auth_header(driver, USERNAME, PASSWORD1)
        if not navigate_to_display(driver):
            print("Fallo login 1, intentando login 2...")
            set_basic_auth_header(driver, USERNAME, PASSWORD2)
            if not navigate_to_display(driver):
                raise Exception("No se pudo acceder al display")

        # Esperar renderizado (máximo 120s como en tu local)
        primer_tag = DATOS_A_BUSCAR[0][0]
        WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.XPATH, f"//div[contains(@title, '{primer_tag}')]"))
        )
        time.sleep(5) # Margen extra
        
        driver.save_screenshot(SCREENSHOT_PATH)

        # Guardar en DB
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM lecturas") # Limpiamos para no acumular (solo última captura)
        
        fecha_actual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        for tag, descripcion in DATOS_A_BUSCAR:
            try:
                elemento = driver.find_element(By.XPATH, f"//div[contains(@title, '{tag}')]")
                valor = (elemento.text or "").strip()
                if not valor:
                    valor = driver.execute_script("return arguments[0].innerText;", elemento).strip()
                
                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha) VALUES (?, ?, ?)", 
                               (descripcion, valor or "Cargando...", fecha_actual))
            except:
                cursor.execute("INSERT INTO lecturas (descripcion, valor, fecha) VALUES (?, ?, ?)", 
                               (descripcion, "No encontrado", fecha_actual))
        
        conn.commit()
        conn.close()
        print("Captura exitosa.")

    except Exception as e:
        print(f"Error: {e}")
        driver.save_screenshot(SCREENSHOT_PATH)
    finally:
        driver.quit()

# =============================================================================
# APP WEB (FLASK)
# =============================================================================

@app.route('/')
def index():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM lecturas", conn)
    conn.close()
    
    html = """
    <html>
        <head>
            <title>Panel PI Vision VPS</title>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
            <meta http-equiv="refresh" content="60">
        </head>
        <body class="container mt-5 bg-light">
            <div class="card shadow">
                <div class="card-header bg-primary text-white d-flex justify-content-between">
                    <h3 class="mb-0">Niveles de Combustible</h3>
                    <span>Auto-refresh: 60s</span>
                </div>
                <div class="card-body">
                    <table class="table table-hover">
                        <thead class="table-dark"><tr><th>Descripción</th><th>Valor</th><th>Última Actualización</th></tr></thead>
                        <tbody>
                            {% for index, row in data.iterrows() %}
                            <tr>
                                <td>{{ row['descripcion'] }}</td>
                                <td><span class="badge bg-success fs-6">{{ row['valor'] }}</span></td>
                                <td class="text-muted">{{ row['fecha'] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                <div class="card-footer text-center">
                    <a href="/debug" class="btn btn-sm btn-outline-secondary">Ver Screenshot Driver</a>
                </div>
            </div>
        </body>
    </html>
    """
    return render_template_string(html, data=df)

@app.route('/debug')
def debug():
    if os.path.exists(SCREENSHOT_PATH):
        return send_file(SCREENSHOT_PATH, mimetype='image/png')
    return "No hay captura disponible", 404

if __name__ == "__main__":
    # Inicializar DB limpia
    if os.path.exists(DB_NAME): os.remove(DB_NAME)
    conn = sqlite3.connect(DB_NAME)
    conn.execute('CREATE TABLE lecturas (descripcion TEXT, valor TEXT, fecha TEXT)')
    conn.close()

    # Programar cada hora
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=ejecutar_scrapping, trigger="interval", hours=1)
    scheduler.start()

    # Primera ejecución
    ejecutar_scrapping()

    app.run(host='0.0.0.0', port=5000)
