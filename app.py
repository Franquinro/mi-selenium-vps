# app.py
import os
import time
import base64
import sqlite3
import re
import html as html_lib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from flask import Flask, render_template_string, send_file
from apscheduler.schedulers.background import BackgroundScheduler

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


app = Flask(__name__)

# -------------------
# CONFIGURACIÓN
# -------------------
TZ = ZoneInfo("Atlantic/Canary")

USERNAME = r"enelint\es43282213p"
PASSWORD1 = os.getenv("SCRAP_PASS1", "")
PASSWORD2 = os.getenv("SCRAP_PASS2", "")

# Persistencia (Coolify Volume montado en /app/data)
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_NAME = os.path.join(DATA_DIR, "temp_niveles.db")
SCREENSHOT_PATH = os.path.join(DATA_DIR, "debug_vps.png")

WINDOW_W, WINDOW_H = 1920, 1080

PI_BASE_URL = "https://eworkerbrrc.endesa.es/PIVision/"
DISPLAY_ID = "88153"
DISPLAY_HASH = f"#/Displays/{DISPLAY_ID}/Balance-Combustible-Bco?mode=kiosk&hidetoolbar&redirect=false"

# Tupla con (tag, descripción, nivel_máximo_metros)
DATOS_A_BUSCAR = (
    (r"\PI-BRRC-S1\BRRC00-0LBL111A", "TANQUE ALMACEN FO", 18),
    (r"\PI-BRRC-S1\BRRC00-0LBL111B", "TANQUE ALMACEN GO A", 18),
    (r"\PI-BRRC-S1\BRRC00-0LBL111C", "TANQUE ALMACEN GO B", 18),
    (r"\PI-BRRC-S1\BRRC036EGD20CL001JT01A", "TANQUE DIARIO GO 1", 13),
    (r"\PI-BRRC-S1\BRRC036EGD20CL002JT01A", "TANQUE DIARIO GO 2", 13),
    (r"\PI-BRRC-S1\BRRC036EGD20CL003JT01A", "TANQUE DIARIO GO 3", 13),
    (r"\PI-BRRC-S1\BRRC0210EGB30CL001JT01A", "TANQUE DIARIO GO 4", 13),
    (r"\PI-BRRC-S1\BRRC00-0LTBM127", "TANQUE GO VAPORES 80MW", 7),
    (r"\PI-JINA-S1\JINA00-145J045822", "NIVEL TANQUE TO2A", 16),
    (r"\PI-JINA-S1\JINAGT-208J021809", "NIVEL TQ GO 2 LM TURBINAS GAS", 13),
    (r"\PI-JINA-S1\JINA00-145J045826", "NIVEL GO DIESEL 4/5", 3),
)

ORDER_TAGS = [t[0] for t in DATOS_A_BUSCAR]
BARRANCO_TAGS = set(ORDER_TAGS[:8])
JINAMAR_TAGS = set(ORDER_TAGS[8:])


# -------------------
# DB
# -------------------
def _db_connect():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db():
    conn = _db_connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lecturas (
            tag TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            valor TEXT NOT NULL,
            ts TEXT NOT NULL,
            nivel_max REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lecturas_tag_ts ON lecturas(tag, ts);")
    conn.close()


# -------------------
# SCRAPING
# -------------------
def set_basic_auth_header(driver, user, password):
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd(
        "Network.setExtraHTTPHeaders",
        {"headers": {"Authorization": f"Basic {token}"}},
    )


def build_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"--window-size={WINDOW_W},{WINDOW_H}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    driver = webdriver.Chrome(options=options)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": ua, "platform": "Windows"})
    return driver


def ejecutar_scrapping():
    ts_now = datetime.now(TZ).isoformat(timespec="seconds")
    print(f"[{ts_now}] Iniciando captura...")

    driver = build_driver()
    try:
        set_basic_auth_header(driver, USERNAME, PASSWORD1)

        driver.get(PI_BASE_URL)
        time.sleep(5)

        target_url = PI_BASE_URL + DISPLAY_HASH
        print(f"Navegando a: {target_url}")
        driver.get(target_url)

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
        driver.save_screenshot(SCREENSHOT_PATH)

        conn = _db_connect()
        cur = conn.cursor()

        for tag, descripcion, nivel_max in DATOS_A_BUSCAR:
            try:
                elemento = driver.find_element(By.XPATH, f"//div[contains(@title, '{tag}')]")
                valor = (elemento.text or "").strip()
                if not valor:
                    valor = driver.execute_script("return arguments[0].innerText;", elemento).strip()

                cur.execute(
                    "INSERT INTO lecturas(tag, descripcion, valor, ts, nivel_max) VALUES (?, ?, ?, ?, ?)",
                    (tag, descripcion, valor or "---", ts_now, float(nivel_max)),
                )
            except Exception:
                cur.execute(
                    "INSERT INTO lecturas(tag, descripcion, valor, ts, nivel_max) VALUES (?, ?, ?, ?, ?)",
                    (tag, descripcion, "Error", ts_now, float(nivel_max)),
                )

        conn.commit()
        conn.close()
        print("Captura finalizada con éxito.")

    except Exception as e:
        print(f"Error detectado: {e}")
        try:
            driver.save_screenshot(SCREENSHOT_PATH)
        except Exception:
            pass
    finally:
        driver.quit()


# -------------------
# PARSEO + TENDENCIA (24h)
# -------------------
_float_re = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def parse_float(valor_texto: str):
    if not valor_texto:
        return None
    m = _float_re.search(valor_texto.replace("³", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except Exception:
        return None


def make_sparkline_svg(values, width=120, height=28, padding=2):
    if not values or len(values) < 2:
        return (
            f'<svg class="sparkline" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            f'<path d="M {padding} {height/2:.2f} L {width-padding} {height/2:.2f}" class="sparkline-path sparkline-flat"/>'
            f"</svg>"
        )

    vmin = min(values)
    vmax = max(values)
    if abs(vmax - vmin) < 1e-9:
        y = height / 2
        return (
            f'<svg class="sparkline" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            f'<path d="M {padding} {y:.2f} L {width-padding} {y:.2f}" class="sparkline-path sparkline-flat"/>'
            f"</svg>"
        )

    usable_w = width - 2 * padding
    usable_h = height - 2 * padding
    step = usable_w / (len(values) - 1)

    pts = []
    for i, val in enumerate(values):
        x = padding + i * step
        t = (val - vmin) / (vmax - vmin)
        y = padding + (1.0 - t) * usable_h
        pts.append((x, y))

    d = f"M {pts[0][0]:.2f} {pts[0][1]:.2f} " + " ".join(
        f"L {x:.2f} {y:.2f}" for x, y in pts[1:]
    )

    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
        f'<path d="{d}" class="sparkline-path"/>'
        f"</svg>"
    )


def build_trends(df_24h: pd.DataFrame):
    """
    Devuelve dict: tag -> {svg, text, cls}
    """
    trends = {}
    if df_24h.empty:
        return trends

    df = df_24h.copy()
    df["dt"] = df["ts"].apply(lambda s: datetime.fromisoformat(s))
    df["num"] = df["valor"].apply(parse_float)
    df = df.dropna(subset=["num"]).sort_values(["tag", "dt"])

    for tag, g in df.groupby("tag"):
        vals = g["num"].tolist()
        if len(vals) < 2:
            trends[tag] = {"svg": make_sparkline_svg([]), "text": "24h: —", "cls": "trend-flat"}
            continue

        delta = vals[-1] - vals[0]
        if abs(delta) < 0.01:
            cls = "trend-flat"
        elif delta > 0:
            cls = "trend-up"
        else:
            cls = "trend-down"

        trends[tag] = {
            "svg": make_sparkline_svg(vals[-96:]),  # 24h a 15 min
            "text": f"24h: {delta:+.2f} m",
            "cls": cls,
        }

    return trends


# -------------------
# BREVO API (EMAIL)
# -------------------
def enviar_email_brevo_api(subject: str, text_content: str, html_content: str):
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    mail_from = os.getenv("MAIL_FROM", "").strip()
    mail_from_name = os.getenv("MAIL_FROM_NAME", "").strip()
    mail_to = [x.strip() for x in os.getenv("MAIL_TO", "").split(",") if x.strip()]

    if not api_key:
        print("Brevo(API): falta BREVO_API_KEY")
        return
    if not mail_from:
        print("Brevo(API): falta MAIL_FROM")
        return
    if not mail_to:
        print("Brevo(API): falta MAIL_TO")
        return

    payload = {
        "sender": {"email": mail_from, **({"name": mail_from_name} if mail_from_name else {})},
        "to": [{"email": m} for m in mail_to],
        "subject": subject,
        "textContent": text_content,
        "htmlContent": html_content,
    }

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": api_key,
    }

    url = "https://api.brevo.com/v3/smtp/email"
    r = requests.post(url, json=payload, headers=headers, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"Brevo(API) error {r.status_code}: {r.text}")

    print("Brevo(API): email enviado OK")


def _fmt_dt_local(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).strftime("%d/%m/%Y %H:%M")


def _fmt_level(val_float):
    if val_float is None:
        return None
    return f"{val_float:.2f}"


def _level_class_from_pct(pct: float) -> str:
    if pct >= 60:
        return "high"
    if pct >= 30:
        return "medium"
    return "low"


def _delta_badge(delta):
    if delta is None:
        return ("—", "#6b7280", "•")
    if abs(delta) < 0.01:
        return (f"{delta:+.2f} m", "#6b7280", "•")
    if delta > 0:
        return (f"{delta:+.2f} m", "#16a34a", "▲")
    return (f"{delta:+.2f} m", "#dc2626", "▼")


def obtener_latest_y_deltas_24h():
    """
    Devuelve:
      - latest_map: tag -> {descripcion, valor(str), dt(datetime), nivel_max}
      - deltas: tag -> delta(float) (numérico) o None
      - capture_dt: datetime (la última ts entre latest)
    """
    conn = _db_connect()

    df_latest = pd.read_sql_query(
        """
        SELECT l.tag, l.descripcion, l.valor, l.ts, l.nivel_max
        FROM lecturas l
        JOIN (
            SELECT tag, MAX(ts) AS max_ts
            FROM lecturas
            GROUP BY tag
        ) m ON l.tag = m.tag AND l.ts = m.max_ts
        """,
        conn,
    )

    ts_min = (datetime.now(TZ) - timedelta(hours=24)).isoformat(timespec="seconds")
    df_24h = pd.read_sql_query(
        "SELECT tag, ts, valor FROM lecturas WHERE ts >= ?",
        conn,
        params=(ts_min,),
    )

    conn.close()

    # deltas
    deltas = {}
    if not df_24h.empty:
        df = df_24h.copy()
        df["dt"] = df["ts"].apply(lambda s: datetime.fromisoformat(s))
        df["num"] = df["valor"].apply(parse_float)
        df = df.dropna(subset=["num"]).sort_values(["tag", "dt"])
        for tag, g in df.groupby("tag"):
            vals = g["num"].tolist()
            deltas[tag] = (vals[-1] - vals[0]) if len(vals) >= 2 else None

    latest_map = {}
    capture_dt = None

    if not df_latest.empty:
        for _, r in df_latest.iterrows():
            try:
                dt = datetime.fromisoformat(str(r["ts"]))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ)
            except Exception:
                dt = None

            latest_map[r["tag"]] = {
                "descripcion": str(r["descripcion"]),
                "valor": str(r["valor"]),
                "dt": dt,
                "nivel_max": float(r["nivel_max"]),
            }

            if dt and (capture_dt is None or dt > capture_dt):
                capture_dt = dt

    return latest_map, deltas, capture_dt


def construir_email_resumen():
    """
    Construye el HTML para el email, compatible con Outlook Desktop (VML + Tablas).
    """
    latest_map, deltas, capture_dt = obtener_latest_y_deltas_24h()

    if capture_dt is None:
        capture_dt = datetime.now(TZ)

    captura_str = _fmt_dt_local(capture_dt)
    subject = f"Niveles combustible - {captura_str}"

    dashboard_url = os.getenv("DASHBOARD_URL", "").strip()

    def build_rows(tags_set):
        rows = []
        for tag, descripcion, nivel_max in DATOS_A_BUSCAR:
            if tag not in tags_set:
                continue

            rec = latest_map.get(tag)
            raw = rec["valor"] if rec else "---"
            max_m = float(rec["nivel_max"]) if rec else float(nivel_max)

            vnum = parse_float(raw) if rec else None
            pct = None
            cls = "error"
            if vnum is not None and max_m > 0:
                pct = (vnum / max_m) * 100.0
                cls = _level_class_from_pct(pct)
            elif raw in ("---", "Error"):
                cls = "error"

            delta = deltas.get(tag)
            delta_txt, delta_color, delta_arrow = _delta_badge(delta)

            rows.append(
                {
                    "name": (rec["descripcion"] if rec else descripcion),
                    "raw": raw,
                    "vnum": vnum,
                    "vnum_str": _fmt_level(vnum),
                    "max_m": max_m,
                    "pct": pct,
                    "pct_str": (f"{pct:.1f}%" if pct is not None else "—"),
                    "cls": cls,
                    "delta_txt": delta_txt,
                    "delta_color": delta_color,
                    "delta_arrow": delta_arrow,
                }
            )
        return rows

    rows_b = build_rows(BARRANCO_TAGS)
    rows_j = build_rows(JINAMAR_TAGS)

    # -------------------
    # TEXTO (fallback)
    # -------------------
    txt_lines = []
    txt_lines.append("Resumen automático de niveles")
    txt_lines.append(f"Lectura: {captura_str}")
    if dashboard_url:
        txt_lines.append(f"Panel: {dashboard_url}")
    txt_lines.append("")
    txt_lines.append("CENTRAL BARRANCO")
    for r in rows_b:
        lvl = f"{r['vnum_str']} m" if r["vnum_str"] is not None else r["raw"]
        txt_lines.append(f"- {r['name']}: {lvl} | {r['pct_str']} | Δ24h {r['delta_txt']}")
    txt_lines.append("")
    txt_lines.append("CENTRAL JINAMAR")
    for r in rows_j:
        lvl = f"{r['vnum_str']} m" if r["vnum_str"] is not None else r["raw"]
        txt_lines.append(f"- {r['name']}: {lvl} | {r['pct_str']} | Δ24h {r['delta_txt']}")

    text_content = "\n".join(txt_lines)

    # -------------------
    # HTML (Outlook Desktop friendly)
    # -------------------
    def badge_html(text: str, bg: str, fg: str = "#ffffff", font_size: int = 13) -> str:
        """
        Badge compatible con Outlook (versión ajustada):
        - Aumentado font_size a 13px (negrita).
        - Aumentado height (h) a 30px para dar más espacio.
        - Añadido padding-top:2px al <td> para empujar visualmente el texto al centro vertical.
        """
        safe = html_lib.escape(text)

        # ancho aproximado (incrementado factor para fuente 13px)
        w = max(110, min(320, 50 + int(len(text) * 9)))
        h = 30  # Altura aumentada

        return f"""<!--[if mso]>
<v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word"
 href="#" style="height:{h}px;width:{w}px;" arcsize="50%" stroke="f" fillcolor="{bg}">
<w:anchorlock/>
<v:textbox inset="0,0,0,0">
  <table cellspacing="0" cellpadding="0" border="0" width="{w}" height="{h}">
    <tr>
      <td align="center" valign="middle" style="padding-top:2px; color:{fg}; font-family:Arial, sans-serif; font-size:{font_size}px; font-weight:bold; text-align:center;">
        {safe}
      </td>
    </tr>
  </table>
</v:textbox>
</v:roundrect>
<![endif]--><!--[if !mso]><!-->
<span style="display:inline-block;background:{bg};color:{fg} !important;padding:7px 14px;border-radius:15px;font-weight:800;font-size:{font_size}px;line-height:16px;white-space:nowrap;font-family:Arial,sans-serif;">
 {safe}
</span>
<!--<![endif]-->"""

    def pct_badge_html(pct_text: str, cls: str) -> str:
        if cls == "high":
            return badge_html(pct_text, "#16a34a", "#ffffff")
        if cls == "medium":
            return badge_html(pct_text, "#d97706", "#ffffff")
        if cls == "low":
            return badge_html(pct_text, "#dc2626", "#ffffff")
        return badge_html(pct_text, "#6b7280", "#ffffff")

    def render_table(rows, header_color, central_name):
        trs = []
        for i, r in enumerate(rows):
            bg = "#ffffff" if i % 2 == 0 else "#f8fafc"

            if r["vnum_str"] is not None:
                lvl = f"""{html_lib.escape(r['vnum_str'])} <span style="color:#6b7280;font-size:12px;">m</span>"""
            else:
                lvl = f"""<span style="color:#6b7280;">{html_lib.escape(r['raw'])}</span>"""

            pct_badge = pct_badge_html(r["pct_str"], r["cls"])
            delta_badge = badge_html(f"{r['delta_arrow']} {r['delta_txt']}", r["delta_color"], "#ffffff")

            trs.append(
                f"""
                <tr bgcolor="{bg}" style="background:{bg};">
                  <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-weight:700;color:#111827;font-family:Arial,sans-serif;">
                    {html_lib.escape(r['name'])}
                  </td>
                  <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:900;color:#111827;white-space:nowrap;font-family:Arial,sans-serif;">
                    {lvl}
                  </td>
                  <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;text-align:right;white-space:nowrap;font-family:Arial,sans-serif;">
                    {pct_badge}
                  </td>
                  <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;text-align:right;white-space:nowrap;font-family:Arial,sans-serif;">
                    {delta_badge}
                  </td>
                </tr>
                """
            )

        label = f"CENTRAL {central_name.upper()}"

        title_block = f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
               style="width:100%;border-collapse:collapse;margin:18px 0 10px 0;mso-table-lspace:0pt;mso-table-rspace:0pt;">
          <tr>
            <td style="padding:0;">
              {badge_html(label, header_color, "#ffffff")}
            </td>
          </tr>
        </table>
        """

        return f"""
        {title_block}
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%"
               style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;mso-table-lspace:0pt;mso-table-rspace:0pt;">
          <thead>
            <tr bgcolor="{header_color}" style="background:{header_color};">
              <th style="padding:10px 12px;text-align:left;color:#ffffff;font-size:12px;letter-spacing:.4px;font-family:Arial,sans-serif;">TANQUE</th>
              <th style="padding:10px 12px;text-align:right;color:#ffffff;font-size:12px;letter-spacing:.4px;font-family:Arial,sans-serif;">NIVEL</th>
              <th style="padding:10px 12px;text-align:right;color:#ffffff;font-size:12px;letter-spacing:.4px;font-family:Arial,sans-serif;">% MAX</th>
              <th style="padding:10px 12px;text-align:right;color:#ffffff;font-size:12px;letter-spacing:.4px;font-family:Arial,sans-serif;">Δ 24H</th>
            </tr>
          </thead>
          <tbody>
            {''.join(trs)}
          </tbody>
        </table>
        """

    panel_line = ""
    if dashboard_url:
        panel_line = f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
               style="width:100%;border-collapse:collapse;margin-top:10px;mso-table-lspace:0pt;mso-table-rspace:0pt;">
          <tr>
            <td style="font-family:Arial,sans-serif;font-size:13px;color:#111827;">
              <a href="{dashboard_url}" style="color:#2563eb;text-decoration:none;font-weight:900;">Abrir panel</a>
              <span style="color:#94a3b8;font-size:12px;">(si tienes acceso desde fuera)</span>
            </td>
          </tr>
        </table>
        """

    html_content = f"""
<!--[if mso]>
<style type="text/css">
  table {{ border-collapse: collapse; }}
  td, th, div, p, a, span {{ font-family: Arial, sans-serif !important; }}
</style>
<![endif]-->

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f1f5f9"
       style="width:100%;background:#f1f5f9;mso-table-lspace:0pt;mso-table-rspace:0pt;">
  <tr>
    <td align="center" style="padding:20px 10px;">

      <table role="presentation" width="760" cellpadding="0" cellspacing="0" border="0" align="center"
             style="width:760px;max-width:760px;background:#ffffff;border:1px solid #e5e7eb;mso-table-lspace:0pt;mso-table-rspace:0pt;">

        <tr>
          <td bgcolor="#203a43" style="background:#203a43;padding:18px;">
            <div style="color:#ffffff;font-size:18px;font-weight:900;letter-spacing:.2px;font-family:Arial,sans-serif;">
              🏭 Monitor de Niveles de Combustible
            </div>
            <div style="color:#cbd5e1;font-size:13px;margin-top:6px;font-family:Arial,sans-serif;">
              Lectura: <span style="color:#ffffff;font-weight:900;">{captura_str}</span>
            </div>
            <div style="color:#94a3b8;font-size:12px;margin-top:4px;font-family:Arial,sans-serif;">
              Incluye Δ 24h por tanque y % sobre máximo
            </div>
          </td>
        </tr>

        <tr>
          <td style="padding:18px;">
            {panel_line}
            {render_table(rows_b, "#2563eb", "Barranco")}
            {render_table(rows_j, "#16a34a", "Jinamar")}

            <div style="margin:14px 0 4px 0;color:#94a3b8;font-size:12px;line-height:1.35;font-family:Arial,sans-serif;">
              Nota: El porcentaje se calcula sobre el máximo configurado en la aplicación. El Δ 24h compara el primer valor numérico disponible con el último en las últimas 24h.
            </div>
          </td>
        </tr>

        <tr>
          <td bgcolor="#f8fafc" style="background:#f8fafc;padding:12px 18px;color:#64748b;font-size:12px;border-top:1px solid #e5e7eb;font-family:Arial,sans-serif;">
            Envío automático · {captura_str}
          </td>
        </tr>

      </table>

    </td>
  </tr>
</table>
"""

    return subject, text_content, html_content


def enviar_resumen_programado():
    try:
        subject, text_content, html_content = construir_email_resumen()
        enviar_email_brevo_api(subject, text_content, html_content)
    except Exception as e:
        print(f"Email: error enviando resumen: {e}")


# -------------------
# WEB
# -------------------
@app.route("/")
def index():
    conn = _db_connect()

    df_latest = pd.read_sql_query(
        """
        SELECT l.tag, l.descripcion, l.valor, l.ts, l.nivel_max
        FROM lecturas l
        JOIN (
            SELECT tag, MAX(ts) AS max_ts
            FROM lecturas
            GROUP BY tag
        ) m ON l.tag = m.tag AND l.ts = m.max_ts
        """,
        conn,
    )

    ts_min = (datetime.now(TZ) - timedelta(hours=24)).isoformat(timespec="seconds")
    df_24h = pd.read_sql_query(
        "SELECT tag, ts, valor FROM lecturas WHERE ts >= ?",
        conn,
        params=(ts_min,),
    )

    conn.close()

    trends = build_trends(df_24h)

    latest_by_tag = {}
    if not df_latest.empty:
        for _, r in df_latest.iterrows():
            latest_by_tag[r["tag"]] = {
                "tag": r["tag"],
                "descripcion": r["descripcion"],
                "valor": r["valor"],
                "ts": r["ts"],
                "nivel_max": r["nivel_max"],
            }

    cards = []
    ultima_captura_dt = None

    for tag, descripcion, nivel_max in DATOS_A_BUSCAR:
        rec = latest_by_tag.get(tag)
        if rec:
            try:
                dt = datetime.fromisoformat(str(rec["ts"]))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ)
            except Exception:
                dt = None

            time_str = dt.astimezone(TZ).strftime("%H:%M") if dt else "--:--"
            if dt and (ultima_captura_dt is None or dt > ultima_captura_dt):
                ultima_captura_dt = dt

            t = trends.get(tag)
            cards.append(
                {
                    "tag": tag,
                    "descripcion": rec["descripcion"],
                    "valor": rec["valor"],
                    "hora": time_str,
                    "nivel_max": rec["nivel_max"],
                    "spark_svg": (t["svg"] if t else make_sparkline_svg([])),
                    "trend_text": (t["text"] if t else "24h: —"),
                    "trend_cls": (t["cls"] if t else "trend-flat"),
                }
            )
        else:
            cards.append(
                {
                    "tag": tag,
                    "descripcion": descripcion,
                    "valor": "---",
                    "hora": "--:--",
                    "nivel_max": float(nivel_max),
                    "spark_svg": make_sparkline_svg([]),
                    "trend_text": "24h: —",
                    "trend_cls": "trend-flat",
                }
            )

    data_barranco = [c for c in cards if c["tag"] in BARRANCO_TAGS]
    data_jinamar = [c for c in cards if c["tag"] in JINAMAR_TAGS]

    ultima_captura_str = (
        ultima_captura_dt.astimezone(TZ).strftime("%d/%m/%Y %H:%M") if ultima_captura_dt else "—"
    )

    html = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monitor Niveles Combustible - PI Vision</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%);
            color: #e0e0e0;
            padding: 12px;
            min-height: 100vh;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .dashboard-header {
            text-align: center;
            margin-bottom: 15px;
            padding: 12px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 10px;
            backdrop-filter: blur(10px);
            box-shadow: 0 3px 15px rgba(0, 0, 0, 0.3);
            width: 80%;
        }
        .dashboard-header h1 {
            font-size: 1.6em;
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 4px;
            text-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
        }
        .dashboard-header .subtitle {
            font-size: 0.8em;
            color: #a0aec0;
            letter-spacing: 0.4px;
        }
        .plant-container {
            background: rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 15px;
            box-shadow: 0 6px 25px rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            width: 80%;
        }
        .plant-title {
            font-size: 1.2em;
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 3px solid;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .plant-container.barranco .plant-title { border-color: #4299e1; }
        .plant-container.jinamar .plant-title { border-color: #48bb78; }
        .plant-icon {
            width: 28px; height: 28px; border-radius: 6px;
            display: flex; align-items: center; justify-content: center;
            font-size: 1.1em; font-weight: bold;
        }
        .plant-container.barranco .plant-icon {
            background: linear-gradient(135deg, #4299e1, #3182ce); color: white;
        }
        .plant-container.jinamar .plant-icon {
            background: linear-gradient(135deg, #48bb78, #38a169); color: white;
        }
        .widgets-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
        }
        .widget {
            background: rgba(255, 255, 255, 0.06);
            border-radius: 10px;
            padding: 14px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            transition: all 0.3s ease;
            box-shadow: 0 3px 12px rgba(0, 0, 0, 0.2);
        }
        .widget:hover {
            transform: translateY(-3px);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
            border-color: rgba(255, 255, 255, 0.15);
        }
        .widget-header {
            display: flex; justify-content: space-between; align-items: flex-start;
            margin-bottom: 10px;
        }
        .tank-name {
            font-weight: 700; font-size: 0.85em; color: #ffffff;
            line-height: 1.2; flex: 1;
        }
        .timestamp {
            font-size: 0.65em; color: #718096; white-space: nowrap;
            margin-left: 8px; padding: 3px 7px;
            background: rgba(0, 0, 0, 0.2); border-radius: 5px;
        }
        .value-display {
            display: flex; align-items: baseline; gap: 6px;
            margin-bottom: 6px;
        }
        .value-number { font-size: 2em; font-weight: 700; color: #ffffff; line-height: 1; }
        .value-unit { font-size: 0.9em; color: #a0aec0; font-weight: 600; }
        .max-indicator { font-size: 0.7em; color: #4299e1; font-weight: 600; margin-bottom: 8px; }
        .level-indicator {
            position: relative; height: 22px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 11px; overflow: hidden;
            box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.3);
        }
        .level-fill {
            height: 100%; border-radius: 11px;
            transition: width 0.8s ease, background 0.3s ease;
            display: flex; align-items: center; justify-content: flex-end;
            padding-right: 10px; font-size: 0.68em; font-weight: 700; color: white;
            text-shadow: 0 1px 3px rgba(0, 0, 0, 0.5);
        }
        .level-low { background: linear-gradient(90deg, #f56565, #e53e3e); }
        .level-medium { background: linear-gradient(90deg, #ed8936, #dd6b20); }
        .level-high { background: linear-gradient(90deg, #48bb78, #38a169); }
        .level-error { background: linear-gradient(90deg, #718096, #4a5568); }

        .trend {
            margin-top: 10px;
            display: flex; align-items: center; justify-content: space-between;
            gap: 10px;
        }
        .sparkline { width: 120px; height: 28px; display: block; }
        .sparkline-path {
            fill: none; stroke-width: 2.2;
            stroke: rgba(255, 255, 255, 0.75);
            stroke-linecap: round; stroke-linejoin: round;
        }
        .sparkline-flat { stroke: rgba(255, 255, 255, 0.35); }
        .trend-meta {
            font-size: 0.72em;
            color: #cbd5e0;
            padding: 3px 8px;
            background: rgba(0,0,0,0.18);
            border-radius: 6px;
            white-space: nowrap;
        }
        .trend-up { color: #9ae6b4; }
        .trend-down { color: #feb2b2; }
        .trend-flat { color: #cbd5e0; }

        @media (max-width: 1600px) { .widgets-grid { grid-template-columns: repeat(3, 1fr); } }
        @media (max-width: 1200px) {
            .widgets-grid { grid-template-columns: repeat(2, 1fr); }
            .dashboard-header, .plant-container { width: 90%; }
        }
        @media (max-width: 768px) {
            .widgets-grid { grid-template-columns: 1fr; }
            .dashboard-header, .plant-container { width: 95%; }
            .dashboard-header h1 { font-size: 1.3em; }
            .value-number { font-size: 1.8em; }
        }
    </style>
</head>
<body>
    <div class="dashboard-header">
        <h1>🏭 Monitor de Niveles de Combustible</h1>
        <div class="subtitle">Sistema PI Vision · Última captura: {{ ultima_captura }}</div>
        <div class="subtitle" style="margin-top:6px;">Persistencia: {{ data_dir }}</div>
    </div>

    <div class="plant-container barranco">
        <div class="plant-title">
            <div class="plant-icon">B</div> PLANTA BARRANCO
        </div>
        <div class="widgets-grid">
            {% for row in data_barranco %}
            <div class="widget">
                <div class="widget-header">
                    <div class="tank-name">{{ row.descripcion }}</div>
                    <div class="timestamp">{{ row.hora }}</div>
                </div>

                <div class="value-display">
                    {% set valor_limpio = row.valor.replace('m', '').replace('³', '').strip() %}
                    <div class="value-number">{{ valor_limpio if valor_limpio not in ['Error', '---'] else row.valor }}</div>
                    <div class="value-unit">{% if valor_limpio not in ['Error', '---'] %}m{% endif %}</div>
                </div>

                <div class="max-indicator">Máximo: {{ row.nivel_max }} m</div>

                <div class="level-indicator">
                    {% set v = row.valor.replace('m', '').replace('³', '').replace(',', '.').strip() %}
                    {% if v.replace('.', '', 1).isdigit() %}
                        {% set nivel_actual = v|float %}
                        {% set nivel_max = row.nivel_max|float %}
                        {% set porcentaje = (nivel_actual / nivel_max * 100)|round(1) %}
                        {% set clase_nivel = 'level-high' if porcentaje >= 60 else ('level-medium' if porcentaje >= 30 else 'level-low') %}
                        <div class="level-fill {{ clase_nivel }}" style="width: {{ porcentaje }}%">{{ porcentaje }}%</div>
                    {% else %}
                        <div class="level-fill level-error" style="width: 100%">{{ row.valor }}</div>
                    {% endif %}
                </div>

                <div class="trend">
                    <div class="spark-wrap">{{ row.spark_svg | safe }}</div>
                    <div class="trend-meta {{ row.trend_cls }}">{{ row.trend_text }}</div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>

    <div class="plant-container jinamar">
        <div class="plant-title">
            <div class="plant-icon">J</div> PLANTA JINAMAR
        </div>
        <div class="widgets-grid">
            {% for row in data_jinamar %}
            <div class="widget">
                <div class="widget-header">
                    <div class="tank-name">{{ row.descripcion }}</div>
                    <div class="timestamp">{{ row.hora }}</div>
                </div>

                <div class="value-display">
                    {% set valor_limpio = row.valor.replace('m', '').replace('³', '').strip() %}
                    <div class="value-number">{{ valor_limpio if valor_limpio not in ['Error', '---'] else row.valor }}</div>
                    <div class="value-unit">{% if valor_limpio not in ['Error', '---'] %}m{% endif %}</div>
                </div>

                <div class="max-indicator">Máximo: {{ row.nivel_max }} m</div>

                <div class="level-indicator">
                    {% set v = row.valor.replace('m', '').replace('³', '').replace(',', '.').strip() %}
                    {% if v.replace('.', '', 1).isdigit() %}
                        {% set nivel_actual = v|float %}
                        {% set nivel_max = row.nivel_max|float %}
                        {% set porcentaje = (nivel_actual / nivel_max * 100)|round(1) %}
                        {% set clase_nivel = 'level-high' if porcentaje >= 60 else ('level-medium' if porcentaje >= 30 else 'level-low') %}
                        <div class="level-fill {{ clase_nivel }}" style="width: {{ porcentaje }}%">{{ porcentaje }}%</div>
                    {% else %}
                        <div class="level-fill level-error" style="width: 100%">{{ row.valor }}</div>
                    {% endif %}
                </div>

                <div class="trend">
                    <div class="spark-wrap">{{ row.spark_svg | safe }}</div>
                    <div class="trend-meta {{ row.trend_cls }}">{{ row.trend_text }}</div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""
    return render_template_string(
        html,
        data_barranco=data_barranco,
        data_jinamar=data_jinamar,
        ultima_captura=ultima_captura_str,
        data_dir=DATA_DIR,
    )


@app.route("/debug")
def debug():
    if os.path.exists(SCREENSHOT_PATH):
        return send_file(SCREENSHOT_PATH, mimetype="image/png")
    return "Captura no disponible", 404


# -------------------
# MAIN
# -------------------
if __name__ == "__main__":
    init_db()

    scheduler = BackgroundScheduler(timezone=TZ)

    # Captura alineada a xx:00, xx:15, xx:30, xx:45
    scheduler.add_job(
        func=ejecutar_scrapping,
        trigger="cron",
        minute="0,15,30,45",
        second=0,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
        replace_existing=True,
        id="captura_niveles_15m",
    )

    # Email a las 04:05, 12:05 y 18:05 (hora Canarias)
    scheduler.add_job(
        func=enviar_resumen_programado,
        trigger="cron",
        hour="4,12,18",
        minute=5,
        second=0,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=900,
        replace_existing=True,
        id="email_resumen_4_12_18",
    )

    scheduler.start()

    # Primera captura al arrancar
    ejecutar_scrapping()

    # Envío inmediato al arrancar (para testear)
    enviar_resumen_programado()

    app.run(host="0.0.0.0", port=5000)
