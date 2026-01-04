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

# --- CONFIGURACIÓN ---
USERNAME = r"enelint\es43282213p"
PASSWORD1 = os.getenv("SCRAP_PASS1", "")
PASSWORD2 = os.getenv("SCRAP_PASS2", "")

DB_NAME = "temp_niveles.db"
SCREENSHOT_PATH = "debug_vps.png"
WINDOW_W, WINDOW_H = 1920, 1080

PI_BASE_URL = "https://eworkerbrrc.endesa.es/PIVision/"
DISPLAY_ID = "88153"
DISPLAY_HASH = f"#/Displays/{DISPLAY_ID}/Balance-Combustible-Bco?mode=kiosk&hidetoolbar&redirect=false"

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

def set_basic_auth_header(driver, user, password):
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {
        "headers": {"Authorization": f"Basic {token}"}
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
    # Anti-detect
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    # User Agent de Escritorio
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": ua, "platform": "Windows"})
    
    return driver

def ejecutar_scrapping():
    print(f"[{datetime.now()}] Iniciando captura...")
    driver = build_driver()
    
    try:
        # Intentar Auth
        set_basic_auth_header(driver, USERNAME, PASSWORD1)
        
        # Navegar a la raíz primero para asegurar el login
        driver.get(PI_BASE_URL)
        time.sleep(5)
        
        # Forzar navegación al display
        target_url = PI_BASE_URL + DISPLAY_HASH
        print(f"Navegando a: {target_url}")
        driver.get(target_url)
        
        # Esperar a que el display cargue (máximo 60s para no saturar el log)
        primer_tag = DATOS_A_BUSCAR[0][0]
        print(f"Esperando tag: {primer_tag}...")
        
        try:
            WebDriverWait(driver, 60).until(
                EC.presence_of_element_located((By.XPATH, f"//div[contains(@title, '{primer_tag}')]"))
            )
        except Exception as te:
            print(f"Timeout esperando elementos. URL actual: {driver.current_url}")
            driver.save_screenshot(SCREENSHOT_PATH)
            raise te

        time.sleep(5)
        driver.save_screenshot(SCREENSHOT_PATH) # Captura de éxito

        # Guardar en DB
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM lecturas")
        fecha_actual = datetime.now().strftime("%H:%M:%S")
        
        for tag, descripcion in DATOS_A_BUSCAR:
            try:
                elemento = driver.find_element(By.XPATH, f"//div[contains(@title, '{tag}')]")
                valor = (elemento.text or "").strip()
                if not valor:
                    valor = driver.execute_script("return arguments[0].innerText;", elemento).strip()
                cursor.execute("INSERT INTO lecturas VALUES (?, ?, ?)", (descripcion, valor or "---", fecha_actual))
            except:
                cursor.execute("INSERT INTO lecturas VALUES (?, ?, ?)", (descripcion, "Error", fecha_actual))
        
        conn.commit()
        conn.close()
        print("Captura finalizada con éxito.")

    except Exception as e:
        print(f"Error detectado: {e}")
        try: driver.save_screenshot(SCREENSHOT_PATH)
        except: pass
    finally:
        driver.quit()

@app.route('/')
def index():
    try:
        conn = sqlite3.connect(DB_NAME)
        df = pd.read_sql_query("SELECT * FROM lecturas", conn)
        conn.close()
        data = df.values.tolist()
    except: data = []
    
    html = """
    <html><head><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"></head>
    <body class="container mt-5">
        <h2>Niveles PI Vision</h2>
        <table class="table">
            <thead><tr><th>Tanque</th><th>Valor</th><th>Hora</th></tr></thead>
            <tbody>
                {% for row in data %}
                <tr><td>{{row[0]}}</td><td><span class="badge bg-success">{{row[1]}}</span></td><td>{{row[2]}}</td></tr>
                {% endfor %}
            </tbody>
        </table>
        <a href="/debug" class="btn btn-secondary">Ver Estado del Navegador</a>
    </body></html>
    """
    return render_template_string(html, data=data)

@app.route('/debug')
def debug():
    if os.path.exists(SCREENSHOT_PATH):
        return send_file(SCREENSHOT_PATH, mimetype='image/png')
    return "Captura no disponible", 404

if __name__ == "__main__":
    # Init DB
    conn = sqlite3.connect(DB_NAME)
    conn.execute('CREATE TABLE IF NOT EXISTS lecturas (descripcion TEXT, valor TEXT, fecha TEXT)')
    conn.close()

    scheduler = BackgroundScheduler()
    scheduler.add_job(func=ejecutar_scrapping, trigger="interval", hours=1)
    scheduler.start()
    ejecutar_scrapping()
    app.run(host='0.0.0.0', port=5000)
