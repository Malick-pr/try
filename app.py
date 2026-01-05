#!/usr/bin/env python3
"""
📊 E-COMMERCE TRACKER - VERSION CLOUD
Hébergé 24/7 sur Railway/Render
"""

import json
import sqlite3
import os
import re
import time
import ssl
from datetime import datetime, timedelta
from threading import Thread, Lock
from urllib.parse import quote
import logging

from flask import Flask, render_template_string, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

# Scraping avec Selenium
try:
    import chromedriver_autoinstaller
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# Apify
try:
    from apify_client import ApifyClient
    APIFY_AVAILABLE = True
except ImportError:
    APIFY_AVAILABLE = False

ssl._create_default_https_context = ssl._create_unverified_context

# ==================== CONFIG ====================
# Variables d'environnement (configurées dans Railway/Render)
APIFY_TOKEN = os.environ.get('APIFY_TOKEN', '')
SEARCH_TERM = os.environ.get('SEARCH_TERM', 'mychariow')
COUNTRIES = os.environ.get('COUNTRIES', 'CI,SN,BJ,BF,GN,CM,GA,ML,TG,NE,CD').split(',')
SYNC_INTERVAL_HOURS = int(os.environ.get('SYNC_INTERVAL', '1'))
ADS_PER_COUNTRY = int(os.environ.get('ADS_PER_COUNTRY', '30'))

# Database
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///tracker.db')
DB_PATH = 'tracker.db'

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# État global
state = {
    'running': False,
    'task': '',
    'progress': 0,
    'total': 0,
    'last_sync': None,
    'next_sync': None,
    'logs': []
}
lock = Lock()


# ==================== DATABASE ====================

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS ads (
        link TEXT PRIMARY KEY,
        ad_id TEXT,
        page TEXT,
        country TEXT,
        product TEXT,
        first_seen TEXT,
        last_seen TEXT,
        status TEXT DEFAULT 'active'
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS stats (
        id INTEGER PRIMARY KEY,
        link TEXT,
        ts TEXT,
        price REAL,
        sales INTEGER,
        ca REAL
    )''')
    
    c.execute('CREATE INDEX IF NOT EXISTS idx_stats_link ON stats(link)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_stats_ts ON stats(ts)')
    
    conn.commit()
    conn.close()
    logger.info("✅ Database initialisée")

def log_msg(msg):
    with lock:
        ts = datetime.now().strftime('%H:%M:%S')
        state['logs'].insert(0, f"[{ts}] {msg}")
        state['logs'] = state['logs'][:100]
    logger.info(msg)


# ==================== SCRAPING FACEBOOK ====================

def sync_facebook():
    """Sync les pubs Facebook via Apify"""
    if not APIFY_TOKEN:
        log_msg("[ERREUR] APIFY_TOKEN non configure!")
        return
    
    if state['running']:
        return
    
    with lock:
        state['running'] = True
        state['task'] = 'Facebook'
        state['progress'] = 0
        state['total'] = len(COUNTRIES)
    
    log_msg("[START] Sync Facebook demarree...")
    
    try:
        client = ApifyClient(APIFY_TOKEN)
        total = 0
        
        for i, country in enumerate(COUNTRIES):
            with lock:
                state['progress'] = i + 1
            
            try:
                url = f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country={country}&q={quote(SEARCH_TERM)}"
                run = client.actor("insight_api_labs/facebook-ad-library-rental").call(run_input={
                    "facebook_ad_library_search_url": url,
                    "total_ads": ADS_PER_COUNTRY,
                    "proxySettings": {"useApifyProxy": True}
                })
                
                if run and "defaultDatasetId" in run:
                    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
                    
                    for ad in items:
                        save_ad(ad, country)
                        total += 1
                    
                    log_msg(f"[OK] {country}: {len(items)} pubs")
                    
            except Exception as e:
                # Nettoyer TOUS les caractères non-ASCII
                err_msg = ''.join(c for c in str(e) if ord(c) < 128)[:50]
                log_msg(f"[ERR] {country}: {err_msg}")
            
            time.sleep(1)
        
        log_msg(f"[DONE] Facebook: {total} pubs importees")
        
    except Exception as e:
        # Nettoyer TOUS les caractères non-ASCII
        err_msg = ''.join(c for c in str(e) if ord(c) < 128)[:50]
        log_msg(f"[ERR] Global: {err_msg}")
    
    finally:
        with lock:
            state['running'] = False
            state['last_sync'] = datetime.now().isoformat()

def save_ad(raw, country):
    """Sauvegarde une pub"""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    snapshot = raw.get('snapshot') or {}
    link = (snapshot.get('link_url') or '').strip()
    if not link:
        conn.close()
        return
    
    ad_id = raw.get('adArchiveID') or ''
    page = raw.get('pageName') or ''
    text = (snapshot.get('body') or {}).get('text') or ''
    product = text.split('\n')[0][:100] if text and '{{' not in text else link.split('/')[-1]
    
    c.execute('SELECT link FROM ads WHERE link = ?', (link,))
    if c.fetchone():
        c.execute('UPDATE ads SET last_seen=?, status="active" WHERE link=?', (now, link))
    else:
        c.execute('INSERT INTO ads VALUES (?,?,?,?,?,?,?,?)',
                 (link, ad_id, page, country, product, now, now, 'active'))
    
    conn.commit()
    conn.close()


# ==================== SCRAPING PRIX (avec Selenium) ====================

def sync_prices():
    """Scrape les prix/ventes avec Selenium"""
    if state['running']:
        return
    
    conn = get_db()
    links = [r['link'] for r in conn.execute(
        'SELECT link FROM ads WHERE status="active" ORDER BY last_seen DESC LIMIT 50'
    ).fetchall()]
    conn.close()
    
    if not links:
        log_msg("[WARN] Aucun lien a scraper")
        return
    
    with lock:
        state['running'] = True
        state['task'] = 'Prix'
        state['progress'] = 0
        state['total'] = len(links)
    
    log_msg(f"[START] Scraping {len(links)} liens...")
    
    # Installer chromedriver
    try:
        import chromedriver_autoinstaller
        chromedriver_autoinstaller.install()
    except Exception as e:
        log_msg(f"[ERR] Chromedriver: {str(e)[:30]}")
    
    success = 0
    for i, link in enumerate(links):
        with lock:
            state['progress'] = i + 1
        
        try:
            price, sales = scrape_with_selenium(link)
            
            if price and sales:
                save_stats(link, price, sales)
                success += 1
                if (i + 1) % 10 == 0:
                    log_msg(f"[PROG] {i+1}/{len(links)} - {success} OK")
        except Exception as e:
            pass
        
        time.sleep(1)
    
    log_msg(f"[DONE] Scraping: {success}/{len(links)} reussis")
    
    with lock:
        state['running'] = False


def scrape_with_selenium(url):
    """Scrape un lien avec Selenium (comme ton code qui marche)"""
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    # Pour Railway/Cloud
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--single-process")
    
    driver = None
    price, sales = None, None
    
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(url)
        
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(3)
        
        # METHODE 1: Chercher "XXX Sales" dans les elements
        try:
            all_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Sales')]")
            for elem in all_elements:
                text = elem.text.strip()
                match = re.search(r"(\d+)\s*Sales", text, re.IGNORECASE)
                if match:
                    sales = int(match.group(1))
                    break
        except:
            pass
        
        # METHODE 2: Chercher dans le HTML source
        if not sales:
            try:
                page_source = driver.page_source
                match = re.search(r"(\d+)\s*Sales", page_source, re.IGNORECASE)
                if match:
                    sales = int(match.group(1))
            except:
                pass
        
        # PRIX: Chercher dans les classes red
        try:
            price_elem = driver.find_element(By.CSS_SELECTOR, "div.text-red-500")
            text = price_elem.text.replace(' ', '').replace(',', '')
            match = re.search(r"(\d+)", text)
            if match:
                price = float(match.group(1))
        except:
            try:
                price_elem = driver.find_element(By.XPATH, "//div[contains(@class, 'text-red')]")
                text = price_elem.text.replace(' ', '').replace(',', '')
                match = re.search(r"(\d+)", text)
                if match:
                    price = float(match.group(1))
            except:
                pass
        
    except Exception as e:
        pass
    
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
    
    return price, sales

def save_stats(link, price, sales):
    """Sauvegarde les stats"""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    ca = price * sales if price and sales else 0
    
    c.execute('INSERT INTO stats (link, ts, price, sales, ca) VALUES (?,?,?,?,?)',
             (link, now, price, sales, ca))
    conn.commit()
    conn.close()


# ==================== SYNC COMPLÈTE ====================

def full_sync():
    """Sync complete: Facebook + Prix + Nettoyage"""
    log_msg("[AUTO] Sync automatique demarree")
    sync_facebook()
    time.sleep(5)
    sync_prices()
    time.sleep(2)
    cleanup_old_ads()  # Nettoyage automatique
    
    with lock:
        next_time = datetime.now() + timedelta(hours=SYNC_INTERVAL_HOURS)
        state['next_sync'] = next_time.strftime('%H:%M')
    
    log_msg(f"[OK] Sync terminee. Prochaine: {state['next_sync']}")


def cleanup_old_ads():
    """Nettoie les pubs inactives et vieilles stats"""
    conn = get_db()
    c = conn.cursor()
    
    # 1. Marquer comme "archived" les pubs pas vues depuis 7 jours
    c.execute('''
        UPDATE ads SET status = 'archived'
        WHERE status = 'active'
        AND datetime(last_seen) < datetime('now', '-7 days')
    ''')
    archived = c.rowcount
    
    # 2. Supprimer les pubs archivees depuis plus de 30 jours
    c.execute('''
        DELETE FROM ads
        WHERE status = 'archived'
        AND datetime(last_seen) < datetime('now', '-30 days')
    ''')
    deleted_ads = c.rowcount
    
    # 3. Supprimer les stats de plus de 30 jours
    c.execute('''
        DELETE FROM stats
        WHERE datetime(ts) < datetime('now', '-30 days')
    ''')
    deleted_stats = c.rowcount
    
    # 4. Supprimer les stats orphelines (pubs supprimees)
    c.execute('''
        DELETE FROM stats
        WHERE link NOT IN (SELECT link FROM ads)
    ''')
    
    conn.commit()
    conn.close()
    
    if archived > 0 or deleted_ads > 0 or deleted_stats > 0:
        log_msg(f"[CLEAN] Archive:{archived} Suppr:{deleted_ads} Stats:{deleted_stats}")


# ==================== API ====================

@app.route('/api/stats')
def api_stats():
    conn = get_db()
    
    total = conn.execute('SELECT COUNT(*) FROM ads').fetchone()[0]
    active = conn.execute('SELECT COUNT(*) FROM ads WHERE status="active"').fetchone()[0]
    
    today = datetime.now().strftime('%Y-%m-%d')
    today_stats = conn.execute('''
        SELECT COUNT(DISTINCT link), COALESCE(SUM(ca), 0)
        FROM stats WHERE ts LIKE ?
    ''', (f'{today}%',)).fetchone()
    
    winners = conn.execute('''
        SELECT COUNT(DISTINCT link) FROM stats WHERE ca > 1000000
    ''').fetchone()[0]
    
    conn.close()
    
    return jsonify({
        'total': total,
        'active': active,
        'scraped': today_stats[0],
        'total_ca': today_stats[1],
        'winners': winners,
        'state': state
    })

@app.route('/api/ads')
def api_ads():
    conn = get_db()
    
    # Classement par DELTA (evolution) au lieu du CA
    ads = conn.execute('''
        SELECT 
            a.link, a.page, a.country, a.product, a.first_seen,
            s1.price, s1.sales, s1.ca, s1.ts as last_ts,
            s2.sales as prev_sales
        FROM ads a
        LEFT JOIN stats s1 ON a.link = s1.link AND s1.id = (
            SELECT id FROM stats WHERE link = a.link ORDER BY ts DESC LIMIT 1
        )
        LEFT JOIN stats s2 ON a.link = s2.link AND s2.id = (
            SELECT id FROM stats WHERE link = a.link ORDER BY ts DESC LIMIT 1 OFFSET 1
        )
        WHERE a.status = 'active'
        ORDER BY (COALESCE(s1.sales, 0) - COALESCE(s2.sales, 0)) DESC
        LIMIT 100
    ''').fetchall()
    
    conn.close()
    
    result = []
    for ad in ads:
        sales = ad['sales'] or 0
        prev = ad['prev_sales'] or 0
        delta = sales - prev if prev else 0
        
        try:
            first = datetime.strptime(ad['first_seen'], '%Y-%m-%d %H:%M')
            age = (datetime.now() - first).days
        except:
            age = 0
        
        result.append({
            'link': ad['link'],
            'page': ad['page'],
            'country': ad['country'],
            'product': ad['product'],
            'price': ad['price'] or 0,
            'sales': sales,
            'delta': delta,
            'ca': ad['ca'] or 0,
            'age': age
        })
    
    return jsonify(result)

@app.route('/api/sync/facebook')
def trigger_facebook():
    if not state['running']:
        Thread(target=sync_facebook, daemon=True).start()
        return jsonify({'status': 'started'})
    return jsonify({'status': 'busy'})

@app.route('/api/sync/prices')
def trigger_prices():
    if not state['running']:
        Thread(target=sync_prices, daemon=True).start()
        return jsonify({'status': 'started'})
    return jsonify({'status': 'busy'})

@app.route('/api/sync/full')
def trigger_full():
    if not state['running']:
        Thread(target=full_sync, daemon=True).start()
        return jsonify({'status': 'started'})
    return jsonify({'status': 'busy'})


# ==================== DASHBOARD ====================

DASHBOARD = '''
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📊 E-Commerce Tracker</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', sans-serif; background: #0f0f1a; color: #fff; min-height: 100vh; }
        
        .header { background: linear-gradient(135deg, #1a1a2e, #16213e); padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #2a2a4a; }
        .logo { font-size: 24px; font-weight: 700; }
        .logo span { background: linear-gradient(90deg, #e94560, #ff6b6b); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        
        .status { display: flex; align-items: center; gap: 15px; }
        .badge { padding: 8px 16px; border-radius: 20px; font-size: 13px; font-weight: 500; display: flex; align-items: center; gap: 8px; }
        .badge.running { background: #28a74533; color: #51cf66; border: 1px solid #28a745; }
        .badge.idle { background: #6c757d33; color: #adb5bd; border: 1px solid #6c757d; }
        .badge .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
        .badge.running .dot { animation: pulse 1s infinite; }
        @keyframes pulse { 50% { opacity: 0.5; } }
        
        .container { max-width: 1400px; margin: 0 auto; padding: 30px; }
        
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: linear-gradient(135deg, #1a1a2e, #16213e); border-radius: 16px; padding: 24px; border: 1px solid #2a2a4a; }
        .stat-card .icon { font-size: 24px; margin-bottom: 10px; }
        .stat-card .label { color: #6c757d; font-size: 13px; margin-bottom: 5px; }
        .stat-card .value { font-size: 32px; font-weight: 700; }
        .stat-card .value.blue { color: #74b9ff; }
        .stat-card .value.green { color: #51cf66; }
        .stat-card .value.red { color: #e94560; }
        .stat-card .value.yellow { color: #ffd43b; }
        
        .actions { display: flex; gap: 12px; margin-bottom: 25px; flex-wrap: wrap; }
        .btn { padding: 12px 24px; border: none; border-radius: 10px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; display: flex; align-items: center; gap: 8px; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 5px 20px rgba(0,0,0,0.3); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .btn-primary { background: linear-gradient(90deg, #e94560, #ff6b6b); color: #fff; }
        .btn-secondary { background: #1a1a2e; color: #fff; border: 1px solid #2a2a4a; }
        
        .progress { background: #1a1a2e; border-radius: 10px; height: 6px; margin-bottom: 25px; overflow: hidden; opacity: 0; transition: opacity 0.3s; }
        .progress.active { opacity: 1; }
        .progress .bar { height: 100%; background: linear-gradient(90deg, #e94560, #ff6b6b); transition: width 0.3s; }
        
        .section { margin-bottom: 30px; }
        .section-title { font-size: 18px; font-weight: 600; margin-bottom: 15px; display: flex; align-items: center; gap: 10px; }
        
        .table-wrap { background: #1a1a2e; border-radius: 16px; overflow: hidden; border: 1px solid #2a2a4a; }
        table { width: 100%; border-collapse: collapse; }
        th { background: #0f0f1a; padding: 14px 16px; text-align: left; font-weight: 600; font-size: 12px; text-transform: uppercase; color: #6c757d; letter-spacing: 0.5px; }
        td { padding: 14px 16px; border-bottom: 1px solid #2a2a4a; font-size: 14px; }
        tr:hover { background: #ffffff05; }
        
        .tag { padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; }
        .tag.winner { background: #e9456033; color: #ff6b6b; }
        .tag.potential { background: #51cf6633; color: #51cf66; }
        .tag.watching { background: #ffd43b33; color: #ffd43b; }
        .tag.new { background: #74b9ff33; color: #74b9ff; }
        
        .delta { font-weight: 600; font-size: 13px; }
        .delta.up { color: #51cf66; }
        .delta.down { color: #e94560; }
        
        .link { color: #74b9ff; text-decoration: none; max-width: 200px; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .link:hover { text-decoration: underline; }
        
        .logs { background: #0a0a12; border-radius: 12px; padding: 16px; max-height: 180px; overflow-y: auto; font-family: 'JetBrains Mono', monospace; font-size: 12px; border: 1px solid #2a2a4a; }
        .log { padding: 4px 0; color: #6c757d; }
        
        .auto-badge { position: fixed; bottom: 20px; right: 20px; background: #1a1a2e; padding: 10px 16px; border-radius: 10px; font-size: 12px; color: #6c757d; border: 1px solid #2a2a4a; }
        
        .empty { text-align: center; padding: 60px; color: #6c757d; }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">📊 E-Commerce <span>Tracker</span></div>
        <div class="status">
            <span id="nextSync" style="color:#6c757d;font-size:13px;">Prochaine sync: --:--</span>
            <div class="badge idle" id="statusBadge">
                <div class="dot"></div>
                <span id="statusText">En attente</span>
            </div>
        </div>
    </div>
    
    <div class="container">
        <div class="stats">
            <div class="stat-card">
                <div class="icon">📈</div>
                <div class="label">Pubs Actives</div>
                <div class="value blue" id="statActive">0</div>
            </div>
            <div class="stat-card">
                <div class="icon">🕷️</div>
                <div class="label">Scrapées</div>
                <div class="value green" id="statScraped">0</div>
            </div>
            <div class="stat-card">
                <div class="icon">🔥</div>
                <div class="label">Winners</div>
                <div class="value red" id="statWinners">0</div>
            </div>
            <div class="stat-card">
                <div class="icon">💰</div>
                <div class="label">CA Total</div>
                <div class="value yellow" id="statCa">0</div>
            </div>
        </div>
        
        <div class="actions">
            <button class="btn btn-primary" onclick="sync('full')">🔄 Sync Complète</button>
            <button class="btn btn-secondary" onclick="sync('facebook')">📡 Facebook</button>
            <button class="btn btn-secondary" onclick="sync('prices')">💰 Prix</button>
        </div>
        
        <div class="progress" id="progress"><div class="bar" id="progressBar"></div></div>
        
        <div class="section">
            <div class="section-title">🏆 Top Produits</div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Status</th>
                            <th>Pays</th>
                            <th>Page</th>
                            <th>Produit</th>
                            <th>Prix</th>
                            <th>Ventes</th>
                            <th>Δ</th>
                            <th>CA</th>
                            <th>Âge</th>
                        </tr>
                    </thead>
                    <tbody id="tableBody">
                        <tr><td colspan="10" class="empty">Chargement...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="section">
            <div class="section-title">📋 Logs</div>
            <div class="logs" id="logs"><div class="log">En attente...</div></div>
        </div>
    </div>
    
    <div class="auto-badge">🔄 Refresh: 10s | Sync auto: toutes les heures</div>
    
    <script>
        const fmt = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(0)+'K' : n;
        
        const tag = (ca, delta, age) => {
            if (ca > 5e6 || delta > 50) return '<span class="tag winner">🔥 Winner</span>';
            if (delta > 10) return '<span class="tag potential">🟢 Potentiel</span>';
            if (delta > 0) return '<span class="tag watching">🟡 Actif</span>';
            if (age < 3) return '<span class="tag new">⚪ Nouveau</span>';
            return '<span class="tag">⚫ Stable</span>';
        };
        
        async function load() {
            try {
                const stats = await (await fetch('/api/stats')).json();
                document.getElementById('statActive').textContent = stats.active;
                document.getElementById('statScraped').textContent = stats.scraped;
                document.getElementById('statWinners').textContent = stats.winners;
                document.getElementById('statCa').textContent = fmt(stats.total_ca);
                
                const badge = document.getElementById('statusBadge');
                const st = stats.state;
                
                if (st.running) {
                    badge.className = 'badge running';
                    document.getElementById('statusText').textContent = st.task + ' ' + st.progress + '/' + st.total;
                    document.getElementById('progress').classList.add('active');
                    document.getElementById('progressBar').style.width = (st.progress/st.total*100) + '%';
                    document.querySelectorAll('.btn').forEach(b => b.disabled = true);
                } else {
                    badge.className = 'badge idle';
                    document.getElementById('statusText').textContent = 'En attente';
                    document.getElementById('progress').classList.remove('active');
                    document.querySelectorAll('.btn').forEach(b => b.disabled = false);
                }
                
                if (st.next_sync) document.getElementById('nextSync').textContent = 'Prochaine: ' + st.next_sync;
                
                document.getElementById('logs').innerHTML = st.logs.map(l => '<div class="log">'+l+'</div>').join('') || '<div class="log">Aucun log</div>';
                
                const ads = await (await fetch('/api/ads')).json();
                if (!ads.length) {
                    document.getElementById('tableBody').innerHTML = '<tr><td colspan="10" class="empty">Aucune pub. Lancez une sync!</td></tr>';
                    return;
                }
                
                document.getElementById('tableBody').innerHTML = ads.map((a, i) => `
                    <tr>
                        <td>${i+1}</td>
                        <td>${tag(a.ca, a.delta, a.age)}</td>
                        <td>${a.country || '-'}</td>
                        <td>${(a.page||'').slice(0,15)}</td>
                        <td><a class="link" href="${a.link}" target="_blank">${(a.product||'').slice(0,30)}</a></td>
                        <td>${a.price ? a.price.toLocaleString() : '-'}</td>
                        <td>${a.sales ? a.sales.toLocaleString() : '-'}</td>
                        <td class="delta ${a.delta>0?'up':a.delta<0?'down':''}">${a.delta>0?'+':''}${a.delta||'-'}</td>
                        <td>${a.ca ? fmt(a.ca) : '-'}</td>
                        <td>${a.age}j</td>
                    </tr>
                `).join('');
            } catch(e) { console.error(e); }
        }
        
        async function sync(type) { await fetch('/api/sync/'+type); load(); }
        
        load();
        setInterval(load, 10000);
    </script>
</body>
</html>
'''

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD)

@app.route('/health')
def health():
    return 'OK'


# ==================== INIT AU DEMARRAGE ====================

# Initialiser la DB immediatement (pas seulement dans __main__)
init_db()
log_msg("[OK] Base de donnees initialisee")

# Scheduler pour sync automatique
scheduler = BackgroundScheduler()
scheduler.add_job(full_sync, 'interval', hours=SYNC_INTERVAL_HOURS, id='auto_sync')
scheduler.start()

# Calculer prochaine sync
next_time = datetime.now() + timedelta(hours=SYNC_INTERVAL_HOURS)
state['next_sync'] = next_time.strftime('%H:%M')

log_msg(f"[SCHED] Auto-sync toutes les {SYNC_INTERVAL_HOURS}h")


# ==================== MAIN ====================

if __name__ == '__main__':
    print("=" * 60)
    print("📊 E-COMMERCE TRACKER - CLOUD VERSION")
    print("=" * 60)
    
    log_msg(f"🌐 Serveur démarré")
    
    # Première sync au démarrage (après 30 sec pour laisser le serveur démarrer)
    def delayed_sync():
        time.sleep(30)
        full_sync()
    
    Thread(target=delayed_sync, daemon=True).start()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
