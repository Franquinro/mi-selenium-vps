import os
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
import time
from datetime import datetime
import subprocess
import sys
from tqdm import tqdm
from colorama import Fore, Style, init
import base64

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Inicializar Colorama
init(autoreset=True)

print(Fore.CYAN + Style.BRIGHT + "\n====================================")
print(Fore.CYAN + Style.BRIGHT + "  Script de Captura de Datos (PI Vision)")
print(Fore.CYAN + Style.BRIGHT + "====================================\n")

# =============================================================================
# CONFIG RÁPIDA
# =============================================================================
HEADLESS = True  # <-- pon False si quieres verlo

WINDOW_W, WINDOW_H = 1920, 1080

PI_BASE_URL = "https://eworkerbrrc.endesa.es/PIVision/"

DISPLAY_ID = "88153"
DISPLAY_SLUG = "Balance-Combustible-Bco"

# Importante: parámetros estilo Android (kiosk + NO redirect)
KIOSK_QUERY = "mode=kiosk&hidetoolbar&redirect=false"

# Hashes objetivo (con y sin slug) + kiosk params
TARGET_HASHES = [
    f"#/Displays/{DISPLAY_ID}/{DISPLAY_SLUG}?{KIOSK_QUERY}",
    f"#/Displays/{DISPLAY_ID}?{KIOSK_QUERY}",
]

# =============================================================================
# CREDENCIALES (por entorno)
# =============================================================================
username = r"enelint\es43282213p"
password1 = os.getenv("PI_PASS1", "")
password2 = os.getenv("PI_PASS2", "")

if not password1 and not password2:
    print(Fore.RED + "No hay contraseñas configuradas. Define PI_PASS1/PI_PASS2 en variables de entorno.")
    input("\nPulse ENTER/RETURN para finalizar el programa.")
    sys.exit(1)

# =============================================================================
# UTILIDADES
# =============================================================================
def abrir_excel(file_path):
    if os.name == 'nt':
        os.startfile(file_path)
    elif os.name == 'posix':
        subprocess.call(['open', file_path] if sys.platform == 'darwin' else ['xdg-open', file_path])


def set_basic_auth_header(driver, user, password):
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {
        "headers": {"Authorization": f"Basic {token}"}
    })


def inject_anti_automation(driver):
    """
    Reduce detección básica en headless.
    IMPORTANTE: debe ejecutarse ANTES de cargar la web.
    """
    script = r"""
    // webdriver -> undefined
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

    // touch points -> 0
    Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});

    // language
    Object.defineProperty(navigator, 'language', {get: () => 'es-ES'});
    Object.defineProperty(navigator, 'languages', {get: () => ['es-ES', 'es', 'en-US', 'en']});
    """
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script})


def configure_desktop_mode(driver):
    """
    Fuerza modo escritorio y evita UI "Reciente/Buscar" tipo móvil en headless.
    - UA normal (sin HeadlessChrome)
    - device metrics desktop (mobile=False)
    - touch emulation off
    - UA-CH metadata (si está disponible) con mobile=False
    """
    version = (driver.capabilities.get("browserVersion") or "").strip()
    major = "120"
    try:
        major = version.split(".")[0]
    except Exception:
        pass

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Safari/537.36"
    )

    driver.execute_cdp_cmd("Network.enable", {})

    # Primero intentamos con userAgentMetadata (UA Client Hints) para asegurar mobile=False
    try:
        driver.execute_cdp_cmd("Network.setUserAgentOverride", {
            "userAgent": ua,
            "acceptLanguage": "es-ES,es;q=0.9,en;q=0.8",
            "platform": "Windows",
            "userAgentMetadata": {
                "brands": [
                    {"brand": "Google Chrome", "version": major},
                    {"brand": "Chromium", "version": major},
                    {"brand": "Not_A Brand", "version": "99"},
                ],
                "fullVersion": version,
                "platform": "Windows",
                "platformVersion": "10.0.0",
                "architecture": "x86",
                "model": "",
                "mobile": False,
            }
        })
    except Exception:
        # Fallback: solo UA clásico
        driver.execute_cdp_cmd("Network.setUserAgentOverride", {
            "userAgent": ua,
            "acceptLanguage": "es-ES,es;q=0.9,en;q=0.8",
            "platform": "Windows",
        })

    # Device metrics desktop
    driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
        "mobile": False,
        "width": WINDOW_W,
        "height": WINDOW_H,
        "deviceScaleFactor": 1
    })

    # Touch OFF
    try:
        driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": False})
    except Exception:
        pass


def build_driver():
    options = webdriver.ChromeOptions()

    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument(f"--window-size={WINDOW_W},{WINDOW_H}")
        # Importante: NO desactivar GPU aquí (PI Vision a veces pinta cosas en canvas)
        # options.add_argument("--disable-gpu")
    else:
        options.add_argument("--start-maximized")
        options.add_argument(f"--window-size={WINDOW_W},{WINDOW_H}")

    options.add_argument("--disable-extensions")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # Reduce banderas típicas de automatización
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)

    try:
        driver.set_window_size(WINDOW_W, WINDOW_H)
    except Exception:
        pass

    # IMPORTANTE: esto tiene que ir antes de cualquier driver.get(...)
    if HEADLESS:
        inject_anti_automation(driver)
        configure_desktop_mode(driver)

    return driver


def is_on_display(driver):
    url = driver.current_url or ""
    return f"/#/Displays/{DISPLAY_ID}" in url


def is_on_recent(driver):
    # En tu captura parece UI de Recientes; esto ayuda a detectarlo
    url = (driver.current_url or "").lower()
    title = (driver.title or "").lower()
    return ("/#/recent" in url) or ("reciente" in title) or ("recent" in title)


def navigate_to_display(driver, timeout=90):
    """
    Navega al display objetivo. Si PI Vision cae en 'Reciente', reintenta.
    """
    for attempt in range(1, 6):
        # Intento 1: URL completa con hash + kiosk params (como Android)
        target_url = PI_BASE_URL + TARGET_HASHES[0]
        driver.get(target_url)

        # Espera inicial
        time.sleep(3)

        # Si ya está en display, OK
        if is_on_display(driver):
            return True

        # Si cayó en "Reciente", forzar hash de nuevo (sin recargar dominio)
        for h in TARGET_HASHES:
            try:
                driver.execute_script("window.location.hash = arguments[0];", h)
                WebDriverWait(driver, 12).until(lambda d: is_on_display(d))
                return True
            except Exception:
                continue

        # Si no, espera y reintenta
        time.sleep(2)

    return False


# =============================================================================
# DATOS A CAPTURAR
# =============================================================================
datos = (
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
# MAIN
# =============================================================================
driver = build_driver()
authenticated = False
resultados = []

def try_password(pw, label):
    if not pw:
        return False

    print(Fore.YELLOW + f"Intentando autenticación {label} ...")
    set_basic_auth_header(driver, username, pw)

    ok = navigate_to_display(driver, timeout=90)
    if not ok:
        print(Fore.RED + "No se pudo abrir el display (se quedó fuera).")
        print(Fore.RED + f"URL actual: {driver.current_url}")
        print(Fore.RED + f"Título actual: {driver.title}")

        # Captura de depuración (muy útil en headless)
        try:
            driver.save_screenshot(f"debug_{label}.png")
            print(Fore.YELLOW + f"Captura guardada: debug_{label}.png")
        except Exception:
            pass
        return False

    return True

try:
    if try_password(password1, "1"):
        authenticated = True
    elif try_password(password2, "2"):
        authenticated = True

    if not authenticated:
        print(Fore.RED + "No se pudo autenticar/abrir el display con ninguna contraseña.")
        input("\nPulse ENTER/RETURN para finalizar el programa.")
        sys.exit(1)

    print(Fore.GREEN + "Display cargado. Iniciando captura de datos...")

    # Esperar a que el display renderice al menos el primer tag
    primer_tag, _ = datos[0]
    try:
        WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.XPATH, f"//div[contains(@title, '{primer_tag}')]"))
        )
    except Exception:
        print(Fore.YELLOW + "No apareció el primer tag a tiempo. Guardando captura y continuo.")
        try:
            driver.save_screenshot("debug_no_tag.png")
            print(Fore.YELLOW + "Captura guardada: debug_no_tag.png")
        except Exception:
            pass

    for tag, descripcion in tqdm(datos, desc=Fore.YELLOW + "Procesando elementos", unit="elemento"):
        try:
            elems = driver.find_elements(By.XPATH, f"//div[contains(@title, '{tag}')]")
            valor = ""
            if elems:
                # A veces el texto tarda en poblarse: reintento corto
                for _ in range(5):
                    valor = (elems[0].text or "").strip()
                    if valor:
                        break
                    time.sleep(0.5)
            if not valor:
                raise Exception("Vacío / no encontrado")

            resultados.append((descripcion, valor))
        except Exception:
            print(Fore.RED + f"No se encontró el elemento para: {descripcion} ({tag})")
            resultados.append((descripcion, "No encontrado"))

finally:
    driver.quit()

if authenticated and resultados:
    df = pd.DataFrame(resultados, columns=['Descripción', 'Valor'])

    fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df.loc[len(df)] = ["Fecha y hora de captura", fecha_hora]

    if getattr(sys, 'frozen', False):
        script_directory = os.path.dirname(sys.executable)
    else:
        script_directory = os.path.dirname(os.path.abspath(__file__))

    output_file = os.path.join(script_directory, "datos_balance.xlsx")

    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Datos')
        worksheet = writer.sheets['Datos']
        worksheet.column_dimensions['A'].width = 50

    print(Fore.GREEN + f"\nArchivo 'datos_balance.xlsx' generado con éxito en {script_directory}.")
    abrir_excel(output_file)

print(Fore.CYAN + "\nProceso completado.")
input("\nPulse ENTER/RETURN para finalizar el programa.")
