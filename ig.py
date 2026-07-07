"""
Cliente do Instagram pro dm_followers. Mesma estratégia do like-bot: dirigir um
Chrome logado via Playwright e fazer as chamadas de dentro da página logada (fetch
same-origin). Endpoints em ../../DM_API_REFERENCE.md.
"""
import json
import re
import time
import random

from playwright.sync_api import sync_playwright

import config
from safety import log, checar_bloqueio, BloqueioDetectado, explicar_status

# ───────────── JS injetado na página logada ─────────────
JS_TOKENS = r"""
() => {
  const html = document.documentElement.innerHTML;
  const pick = (re) => { const m = html.match(re); return m ? m[1] : null; };
  const cookie = (n) => {
    const m = document.cookie.match(new RegExp('(?:^|; )' + n + '=([^;]+)'));
    return m ? decodeURIComponent(m[1]) : null;
  };
  const dtsg = pick(/"DTSGInitialData",\[\],\{"token":"([^"]+)"/)
            || pick(/"dtsg":\{"token":"([^"]+)"/)
            || pick(/name="fb_dtsg" value="([^"]+)"/);
  const lsd = pick(/"LSD",\[\],\{"token":"([^"]+)"/)
            || pick(/"lsd":\{"token":"([^"]+)"/);
  const av = pick(/"actorID":"(\d+)"/) || pick(/"IG_USER_EIMU":"(\d+)"/)
          || pick(/"viewerId":"(\d+)"/) || cookie('ds_user_id');
  let claim = '0';
  try { claim = sessionStorage.getItem('www-claim-v2') || '0'; } catch (e) {}
  return { dtsg, lsd, av, claim, csrf: cookie('csrftoken'), dsuser: cookie('ds_user_id') };
}
"""

JS_GRAPHQL = r"""
async (p) => {
  const body = new URLSearchParams();
  body.set('av', p.av);
  body.set('__a', '1');
  body.set('__comet_req', '7');
  body.set('dpr', '1');
  body.set('fb_dtsg', p.dtsg);
  body.set('jazoest', p.jazoest);
  body.set('lsd', p.lsd);
  body.set('fb_api_caller_class', 'RelayModern');
  body.set('fb_api_req_friendly_name', p.friendly);
  body.set('server_timestamps', 'true');
  body.set('doc_id', p.doc_id);
  body.set('variables', p.variables);
  const r = await fetch(p.endpoint || '/api/graphql', {
    method: 'POST', credentials: 'include', headers: {
      'content-type': 'application/x-www-form-urlencoded',
      'x-fb-friendly-name': p.friendly, 'x-csrftoken': p.csrf,
      'x-asbd-id': p.asbd, 'x-ig-app-id': p.appid,
    }, body: body.toString() });
  return { status: r.status, text: await r.text() };
}
"""

JS_CREATE_THREAD = r"""
async (p) => {
  const body = new URLSearchParams();
  body.set('recipient_users', '["' + p.pk + '"]');
  body.set('fb_dtsg', p.dtsg);
  body.set('jazoest', p.jazoest);
  const r = await fetch('/api/v1/direct_v2/create_group_thread/', {
    method: 'POST', credentials: 'include', headers: {
      'content-type': 'application/x-www-form-urlencoded',
      'x-ig-app-id': p.appid, 'x-asbd-id': p.asbd, 'x-csrftoken': p.csrf,
      'x-requested-with': 'XMLHttpRequest', 'x-ig-www-claim': p.claim,
    }, body: body.toString() });
  return { status: r.status, text: await r.text() };
}
"""


def _jazoest(dtsg):
    return "2" + str(sum(ord(c) for c in dtsg)) if dtsg else ""


def _parse_json(text):
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]
    return json.loads(text)


def _otid():
    """offline_threading_id: inteiro grande único por mensagem."""
    return str(int(time.time() * 1000) * 1000 + random.randint(0, 999999))


class IG:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self._pw = None
        self.ctx = None
        self.page = None
        self.tokens = {}

    # ─────────── ciclo de vida ───────────
    def abrir(self):
        self._pw = sync_playwright().start()
        kwargs = dict(
            headless=config.HEADLESS, locale=config.LOCALE, user_agent=config.USER_AGENT,
            viewport={"width": 1280, "height": 820},
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"])
        if getattr(config, "PROXY", None):
            kwargs["proxy"] = config.PROXY
            log.info("🌐 Proxy ativo: %s", config.PROXY.get("server"))
        if getattr(config, "USAR_CHROME_REAL", False):
            kwargs["channel"] = "chrome"
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(config.USER_DATA_DIR, **kwargs)
        except Exception as e:
            if "channel" in kwargs:
                log.warning("Chrome real não encontrado (%s); usando Chromium.", e)
                kwargs.pop("channel")
                self.ctx = self._pw.chromium.launch_persistent_context(config.USER_DATA_DIR, **kwargs)
            else:
                raise
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        return self

    def fechar(self):
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def __enter__(self):
        return self.abrir()

    def __exit__(self, *a):
        self.fechar()

    # ─────────── sessão ───────────
    def ir(self, url):
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(1500)

    def _cookies(self):
        try:
            cks = self.ctx.cookies("https://www.instagram.com")
        except Exception:
            cks = self.ctx.cookies()
        return {c["name"]: c["value"] for c in cks}

    def logado(self):
        return bool(self._cookies().get("sessionid"))

    def importar_cookies(self, cookies):
        """Injeta cookies (do navegador normal) no perfil — evita o login/reCAPTCHA."""
        self.ctx.add_cookies(cookies)
        self.ir("https://www.instagram.com/")
        return self.logado()

    def carregar_tokens(self):
        self.tokens = self.page.evaluate(JS_TOKENS)
        ck = self._cookies()
        self.tokens["csrf"] = self.tokens.get("csrf") or ck.get("csrftoken")
        self.tokens["av"] = self.tokens.get("av") or ck.get("ds_user_id")
        self.tokens["jazoest"] = _jazoest(self.tokens.get("dtsg") or "")
        falta = [k for k in ("csrf", "dtsg", "lsd") if not self.tokens.get(k)]
        if falta:
            log.warning("Tokens ausentes: %s — confira se a página carregou logada.", falta)
        return self.tokens

    def _base(self):
        return {"appid": config.IG_APP_ID, "asbd": config.ASBD_ID,
                "csrf": self.tokens.get("csrf"), "claim": self.tokens.get("claim", "0"),
                "av": self.tokens.get("av"), "dtsg": self.tokens.get("dtsg"),
                "lsd": self.tokens.get("lsd"), "jazoest": self.tokens.get("jazoest")}

    # ─────────── operações ───────────
    def novos_seguidores(self):
        """Lê a aba de notificações e devolve quem 'começou a seguir você',
        do mais antigo pro mais novo: [{pk, username, timestamp}]."""
        res = self.page.evaluate(JS_GRAPHQL, {
            **self._base(), "endpoint": "/graphql/query",
            "friendly": "PolarisActivityFeedStoriesViewQuery", "doc_id": config.DOC_ACTIVITY,
            "variables": json.dumps({"inbox_request_data": {}, "pending_request_data": {}},
                                    separators=(",", ":"))})
        checar_bloqueio(res["status"], res["text"])
        data = _parse_json(res["text"])
        try:
            inbox = data["data"]["xdt_activity_inbox"]
        except (KeyError, TypeError):
            log.error("Feed de atividades em formato inesperado. Rode com --debug.")
            return []
        stories = (inbox.get("new_stories") or []) + (inbox.get("old_stories") or [])
        out = []
        for s in stories:
            if s.get("type") != 3:                      # 3 = "começou a seguir você"
                continue
            args = s.get("args") or {}
            users = args.get("users") or []
            if not users:
                continue
            u = users[0]
            out.append({"pk": str(u.get("pk") or u.get("id")),
                        "username": u.get("username", "?"),
                        "timestamp": float(args.get("timestamp") or 0)})
        out.sort(key=lambda x: x["timestamp"])          # antigo -> novo
        return out

    def criar_thread(self, pk):
        """Cria/abre a DM com o usuário; retorna thread_v2_id."""
        res = self.page.evaluate(JS_CREATE_THREAD, {**self._base(), "pk": str(pk)})
        checar_bloqueio(res["status"], res["text"])
        j = _parse_json(res["text"])
        return j.get("thread_v2_id") or (j.get("thread") or {}).get("thread_v2_id")

    def enviar_dm(self, thread_v2_id, texto):
        """Envia o texto na thread. Retorna o dict de resposta."""
        variables = {
            "ig_thread_igid": str(thread_v2_id),
            "offline_threading_id": _otid(),
            "recipient_igids": None, "replied_to_client_context": None,
            "replied_to_item_id": None, "reply_to_message_id": None, "sampled": None,
            "text": {"sensitive_string_value": texto},
            "mentions": [], "mentioned_user_ids": [], "commands": None,
            "forwarded_from_thread_id": None, "is_forwarded_from_own_message": None,
            "send_attribution": "igd_web_chat_tab:in_thread",
        }
        res = self.page.evaluate(JS_GRAPHQL, {
            **self._base(), "endpoint": "/api/graphql",
            "friendly": "IGDirectTextSendMutation", "doc_id": config.DOC_DM_SEND,
            "variables": json.dumps(variables, separators=(",", ":"))})
        checar_bloqueio(res["status"], res["text"])
        try:
            return _parse_json(res["text"])
        except Exception:
            st = res.get("status")
            corpo = (res.get("text") or "").strip()
            detalhe = f'o IG respondeu: "{corpo[:150]}"' if corpo else "o IG não retornou nada (corpo vazio)"
            raise BloqueioDetectado(f"envio travado — HTTP {st}: {explicar_status(st)}. {detalhe}")
