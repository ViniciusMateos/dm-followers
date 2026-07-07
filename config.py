"""
Configuração do worker dm_followers — manda DM pros novos seguidores.

Lê a aba de notificações ("começou a seguir você"), processa do mais antigo pro
mais recente, manda a mensagem (com o nick da pessoa na 1ª linha) e salva o último
processado pra retomar. DM é o automatismo de MAIOR risco de ban — caps minúsculos.

Endpoints em ../../DM_API_REFERENCE.md.
"""
import os

_BASE = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────── Sessão / navegador ─────────────────
USER_DATA_DIR = os.path.join(_BASE, "browser_profile")


# ─────────────────────── Proxy (opcional, configurável pelo app) ──────────
# Grava proxy.json {enabled, server, username, password}. Formato do Playwright.
def _carregar_proxy():
    import json
    f = os.path.join(_BASE, "proxy.json")
    if os.path.exists(f):
        try:
            d = json.load(open(f, encoding="utf-8"))
            if d.get("enabled") and d.get("server"):
                return {k: d[k] for k in ("server", "username", "password") if d.get(k)}
        except Exception:
            pass
    return None


PROXY = _carregar_proxy()
HEADLESS = False
USAR_CHROME_REAL = True       # usa o Chrome instalado (menos detectável, ajuda no login/reCAPTCHA)
LOCALE = "pt-BR"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# ───────────────── Constantes da API (da captura) ───────────
IG_APP_ID = "936619743392459"
ASBD_ID = "359341"
DOC_ACTIVITY = "26398841236455905"     # PolarisActivityFeedStoriesViewQuery (/graphql/query)
DOC_DM_SEND = "26911679871773184"      # IGDirectTextSendMutation (/api/graphql)

# ───────────────────── MENSAGEM ─────────────────────────────
# {username} é trocado pelo nick do destinatário. (Mensagem fixa, sem variação.)
# Ainda suporta spintax {a|b|c} se um dia quiser variar — mas aqui está fixa.
MENSAGEM = (
    "{username},\n\n"
    "Siga o @brechoquasenadaa pra acompanhar os próximos drops!!\n\n"
    "Primeira compra no brechó tem desconto de 10% em qualquer item!"
)

# ───────────────────── LIMITES DE SEGURANÇA ─────────────────
# Lista pequena (<50) → caps generosos pra processar tudo numa run. O kill-switch
# segue ligado: se o IG bloquear no meio, para na hora e salva de onde parou.
APLICAR_CAPS = True
MAX_DMS_DIA = 0             # 0 = SEM cap diário
MAX_DMS_HORA = 0           # 0 = SEM cap horário
MAX_DMS_POR_RUN = 0        # 0 = SEM limite por execução (manda todos os novos de uma vez)

DELAY_DM = (5, 20)          # entre uma pessoa e outra
PAUSA_LONGA_CADA = 0        # 0 = SEM pausa longa
PAUSA_LONGA = (120, 300)    # (ignorado se PAUSA_LONGA_CADA = 0)
DELAY_ACAO_UI = (1.5, 4.0)  # dwell ao abrir perfil / conversa

ACTIVE_HOURS = (9, 23)       # só roda em horário humano (com APLICAR_CAPS=True)

# 1ª RUN: de qual seguidor começar (do mais antigo dele pro mais recente).
# Depois disso ele salva o último e retoma sozinho (ignora este valor).
COMECAR_DE = "n.mondra"

# Comportamento fixo: só processa "começou a seguir você" — o filtro é hardcoded em
# ig.novos_seguidores (type == 3). Esta flag não é lida, fica só de registro.
SO_NOVOS_SEGUIDORES = True

# ─────────────────────────── Paths ──────────────────────────
OUTPUT_DIR = os.path.join(_BASE, "output")
STATE_FILE = os.path.join(OUTPUT_DIR, "state.json")
LOG_FILE = os.path.join(OUTPUT_DIR, "run.log")
