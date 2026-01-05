# 📊 E-Commerce Tracker - Version Cloud

Dashboard de veille e-commerce qui tourne 24/7 en ligne.

## 🚀 Déploiement sur Railway (Recommandé)

### Étape 1: Créer un compte Railway
1. Va sur https://railway.app
2. Connecte-toi avec GitHub

### Étape 2: Déployer
1. Clique sur **"New Project"**
2. Choisis **"Deploy from GitHub repo"**
3. Connecte ton repo (ou utilise "Empty Project" puis upload)

### Étape 3: Configurer les variables d'environnement
Dans Railway, va dans **Variables** et ajoute:

```
APIFY_TOKEN=ton_token_apify_ici
SEARCH_TERM=mychariow
COUNTRIES=CI,SN,BJ,BF,GN,CM,GA,ML,TG,NE,CD
SYNC_INTERVAL=1
ADS_PER_COUNTRY=30
```

### Étape 4: C'est tout!
- Railway te donne une URL publique (ex: `https://ton-app.up.railway.app`)
- Le dashboard tourne 24/7
- Sync automatique toutes les heures

---

## 🌐 Déploiement sur Render

### Étape 1: Créer un compte
1. Va sur https://render.com
2. Connecte-toi avec GitHub

### Étape 2: Créer un Web Service
1. Clique **"New" → "Web Service"**
2. Connecte ton repo GitHub
3. Configure:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT`

### Étape 3: Variables d'environnement
Ajoute les mêmes variables que Railway.

---

## 💰 Coûts estimés

| Plateforme | Plan | Coût |
|------------|------|------|
| Railway | Starter | ~$5/mois |
| Render | Free | Gratuit (s'éteint après 15min) |
| Render | Starter | $7/mois |

---

## 📱 Fonctionnalités

- ✅ Dashboard web accessible partout
- ✅ Sync automatique toutes les heures
- ✅ Scraping Facebook Ads via Apify
- ✅ Scraping prix/ventes des sites
- ✅ Calcul du CA et évolution (Delta)
- ✅ Identification des Winners
- ✅ Logs en temps réel
- ✅ Interface moderne style Minea

---

## 🔧 Variables d'environnement

| Variable | Description | Exemple |
|----------|-------------|---------|
| `APIFY_TOKEN` | Ton token API Apify | `apify_api_xxx` |
| `SEARCH_TERM` | Terme de recherche | `mychariow` |
| `COUNTRIES` | Liste des pays (virgule) | `CI,SN,BJ` |
| `SYNC_INTERVAL` | Heures entre chaque sync | `1` |
| `ADS_PER_COUNTRY` | Pubs par pays | `30` |

---

## 📞 Support

Si tu as des questions, vérifie:
1. Les logs dans Railway/Render
2. Que ton token Apify est valide
3. Que tu as assez de crédits Apify
