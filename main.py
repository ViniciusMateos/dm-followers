"""
dm_followers — manda DM pros novos seguidores (aba de notificações).

Fluxo:
  1. lê a aba de notificações ("começou a seguir você"), do mais antigo pro mais novo
  2. na 1ª vez você escolhe de qual seguidor começar (--start-from @user)
  3. manda a mensagem (template com o nick da pessoa na 1ª linha + spintax)
  4. salva o último processado; no próximo run pega só os mais novos

Uso:
  python main.py --login                  # 1ª vez: login manual
  python main.py --dry-run --start-from juliatilco   # simula a partir de um user
  python main.py --start-from juliatilco  # 1ª vez pra valer (escolhe de quem começa)
  python main.py                          # próximos runs: só os novos desde o último
  python main.py --debug                  # dump do feed de atividades

Modular (modos de tempo):
  python main.py --listar-modos           # mostra os modos (padrao/agressivo/calmo)
  python main.py --modo calmo             # roda com tempos mais lentos (recomendado p/ DM)
  python main.py --modo agressivo --start-from fulano
"""
import argparse
import os
import re
import sys
import random
import time
import traceback
from datetime import datetime

import config
import perfis
from safety import State, Guard, log, BloqueioDetectado, LimiteAtingido
from ig import IG

LOGS_ERRO_DIR = os.path.join(config.OUTPUT_DIR, "logs")

_T_INICIO = time.monotonic()


def _dur_run():
    """Tempo total desta execução, formatado (ex: '3m 12s')."""
    s = int(time.monotonic() - _T_INICIO)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


def progresso(done, total, label=""):
    """Marcador machine-readable pro backend/app desenharem a barra de progresso."""
    print(f"[progress] {done} {total} {label}".rstrip(), flush=True)


def _carregar_cookies(path):
    """Lê um JSON de cookies (ex: extensão Cookie-Editor) e converte pro formato Playwright."""
    import json
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]
    ss_map = {"no_restriction": "None", "unspecified": "Lax", "lax": "Lax",
              "strict": "Strict", "none": "None"}
    out = []
    for c in raw:
        ck = {"name": c["name"], "value": c["value"],
              "domain": c.get("domain") or ".instagram.com", "path": c.get("path", "/"),
              "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure", True)),
              "sameSite": ss_map.get(str(c.get("sameSite", "")).lower(), "Lax")}
        exp = c.get("expirationDate") or c.get("expires")
        if exp and not c.get("session"):
            ck["expires"] = int(float(exp))
        out.append(ck)
    return out


def modo_importar_cookies(path):
    cookies = _carregar_cookies(path)
    log.info("Importando %d cookies de %s…", len(cookies), path)
    with IG() as ig:
        if ig.importar_cookies(cookies):
            log.info("✓ Sessão logada! Pode rodar --dry-run.")
        else:
            log.warning("Importou, mas não achei sessionid. Exporte os cookies do instagram.com "
                        "COM a conta logada.")


def montar_mensagem(username):
    """Troca {username} e resolve spintax {a|b|c} (escolhe um aleatório)."""
    txt = config.MENSAGEM.replace("{username}", username)
    while re.search(r"\{[^{}]*\|[^{}]*\}", txt):
        txt = re.sub(r"\{([^{}]*\|[^{}]*)\}",
                     lambda m: random.choice(m.group(1).split("|")), txt, count=1)
    return txt


def imprimir_saldo(guard, motivo=""):
    extra = f" — {motivo}" if motivo else ""
    log.info("──────────────── SALDO DA EXECUÇÃO%s ────────────────", extra)
    log.info("   DMs enviadas .......... %d", guard.enviadas)
    log.info("   puladas (já enviou) ... %d", guard.puladas)
    log.info("   tempo de execução ..... %s", _dur_run())
    log.info("─────────────────────────────────────────────────────")
    # marcador machine-readable pro histórico (vai pro stdout E pro run.log)
    log.info("[saldo] enviadas=%d puladas=%d", guard.enviadas, guard.puladas)


def tratar_erro(exc, titulo):
    os.makedirs(LOGS_ERRO_DIR, exist_ok=True)
    caminho = os.path.join(LOGS_ERRO_DIR, "erro_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
    try:
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        caminho = "(não consegui salvar o arquivo de erro)"
    log.error("%s: %s", titulo, str(exc)[:160])
    log.error("   detalhes completos em: %s", caminho)


def modo_login():
    log.info("Abrindo navegador para login manual…")
    with IG() as ig:
        ig.ir("https://www.instagram.com/")
        input(">>> Loga na janela do Chrome e aperte ENTER aqui quando estiver no feed… ")
        log.info("Sessão detectada." if ig.logado() else "Não detectei sessionid — confira o login.")


def escolher_candidatos(novos, state, start_from, start_oldest):
    """Aplica a regra de retomada. novos vêm do mais antigo pro mais novo."""
    nao_enviados = [f for f in novos if not state.ja_enviou(f["pk"])]
    if start_from:
        idx = next((i for i, f in enumerate(novos) if f["username"].lower() == start_from.lower()), None)
        if idx is None:
            log.error("--start-from %s: não achei esse usuário no feed de notificações.", start_from)
            return []
        ts = novos[idx]["timestamp"]
        return [f for f in nao_enviados if f["timestamp"] >= ts]
    last = state.data.get("last_timestamp", 0)
    if last > 0:
        return [f for f in nao_enviados if f["timestamp"] > last]
    if start_oldest:
        return nao_enviados
    log.warning("Primeira vez: escolhe de quem começar com --start-from <username> "
                "(ou --start-from-oldest pra mandar pra todos os visíveis). Não vou agir sozinho.")
    return []


def run(dry=False, start_from=None, start_oldest=False, debug=False, ignorar_janela=False):
    state = State()
    guard = Guard(state, dry_run=dry)
    try:
        guard.checar_janela(ignorar=ignorar_janela)
    except LimiteAtingido as e:
        log.info("Não vou rodar agora: %s", e)
        return

    log.info("Abrindo Instagram (%s)…", "DRY-RUN" if dry else "AÇÃO REAL")
    with IG(dry_run=dry) as ig:
        ig.ir("https://www.instagram.com/")
        if not ig.logado():
            log.error("Sem sessão logada. Rode `python main.py --login` primeiro.")
            return
        ig.carregar_tokens()

        try:
            novos = ig.novos_seguidores()
            log.info("%d novos seguidores no feed de notificações.", len(novos))
            if debug:
                os.makedirs(config.OUTPUT_DIR, exist_ok=True)
                import json
                with open(os.path.join(config.OUTPUT_DIR, "debug_seguidores.json"), "w", encoding="utf-8") as f:
                    json.dump(novos, f, ensure_ascii=False, indent=1)
                log.info("debug: feed salvo em output/debug_seguidores.json")

            # 1ª run (sem nada salvo): usa o COMECAR_DE do config se nada foi passado
            primeira_vez = state.data.get("last_timestamp", 0) == 0
            if not start_from and not start_oldest and primeira_vez and getattr(config, "COMECAR_DE", None):
                start_from = config.COMECAR_DE
                log.info("Primeira run: começando a partir de @%s (config COMECAR_DE).", start_from)

            candidatos = escolher_candidatos(novos, state, start_from, start_oldest)
            # MAX_DMS_POR_RUN = 0 (ou caps off) → manda pra todos os novos
            limite = (config.MAX_DMS_POR_RUN if config.APLICAR_CAPS else 0) or len(candidatos)
            candidatos = candidatos[:limite]
            if not candidatos:
                log.info("Ainda não tem novos seguidores pra mandar DM. 👋")
                return
            log.info("Vão receber DM (%d): %s", len(candidatos),
                     ", ".join("@" + c["username"] for c in candidatos[:10]) +
                     (" …" if len(candidatos) > 10 else ""))

            total = len(candidatos)
            progresso(0, total, "iniciando")
            for i, c in enumerate(candidatos):
                progresso(i, total, f"@{c['username']}")
                guard.pode_enviar()
                texto = montar_mensagem(c["username"])
                # navega como humano: abre o perfil da pessoa (com uma dwell)
                ig.ir(f"https://www.instagram.com/{c['username']}/")
                guard.dormir(config.DELAY_ACAO_UI, "abrindo perfil")
                if dry:
                    log.info("│ [dry] DM → @%s (pk %s)", c["username"], c["pk"])
                    log.info("│       %s", texto.replace("\n", " ⏎ ")[:120])
                    guard.enviadas += 1; guard.pos_dm_dry()
                    continue
                thread = ig.criar_thread(c["pk"])
                if not thread:
                    log.warning("! não consegui abrir thread com @%s — pulando", c["username"])
                    continue
                # abre a conversa antes de mandar (humano)
                ig.ir(f"https://www.instagram.com/direct/t/{thread}/")
                guard.dormir(config.DELAY_ACAO_UI, "abrindo conversa")
                ig.enviar_dm(thread, texto)        # levanta BloqueioDetectado se falhar
                state.marcar_enviado(c["pk"], c["timestamp"])
                guard.enviadas += 1
                log.info("✓ DM enviada → @%s", c["username"])
                guard.pos_dm()
            progresso(total, total, "concluído")
        except LimiteAtingido as e:
            log.info("Parando (cap atingido): %s", e)
        except BloqueioDetectado as e:
            tratar_erro(e, "BLOQUEIO do Instagram — parando o run")
        except KeyboardInterrupt:
            log.info("Interrompido manualmente (Ctrl+C).")
        except Exception as e:
            tratar_erro(e, "erro inesperado — parando o run")
        finally:
            imprimir_saldo(guard, "simulado" if dry else "")


def main():
    ap = argparse.ArgumentParser(description="dm_followers")
    ap.add_argument("--login", action="store_true", help="login manual (1ª vez)")
    ap.add_argument("--import-cookies", metavar="FILE", help="importa cookies (JSON do Cookie-Editor) e pula o login")
    ap.add_argument("--dry-run", action="store_true", help="simula sem enviar")
    ap.add_argument("--start-from", metavar="USER", help="1ª vez: começar a partir desse seguidor")
    ap.add_argument("--start-from-oldest", action="store_true", help="manda pra todos os visíveis")
    ap.add_argument("--debug", action="store_true", help="dump do feed de atividades")
    ap.add_argument("--ignore-window", action="store_true", help="ignora janela de horário")
    # ── modularização (modos de tempo) ──
    ap.add_argument("--modo", metavar="NOME", default="padrao", help="modo: padrao, agressivo, calmo…")
    ap.add_argument("--listar-modos", action="store_true", help="lista os modos salvos e sai")
    a = ap.parse_args()

    if a.listar_modos:
        for nome, p in perfis.carregar_perfis().items():
            log.info("modo: %-12s caps=%s | dms/run=%s | delay_dm=%s | pausa_cada=%s",
                     nome, p["aplicar_caps"], p["max_dms_por_run"],
                     p["delay_dm"], p["pausa_longa_cada"])
        return
    if a.import_cookies:
        modo_importar_cookies(a.import_cookies)
        return
    if a.login:
        modo_login()
        return

    # aplica o MODO escolhido no config antes de rodar
    perfil = perfis.get_perfil(a.modo)
    if not perfil:
        log.error("Modo '%s' não existe. Use --listar-modos.", a.modo)
        sys.exit(2)
    perfis.aplicar(config, perfil)
    log.info("Modo: %s  |  delay_dm: %s  |  caps: %s", a.modo,
             config.DELAY_DM, config.APLICAR_CAPS)

    try:
        run(dry=a.dry_run, start_from=a.start_from, start_oldest=a.start_from_oldest,
            debug=a.debug, ignorar_janela=a.ignore_window)
    except KeyboardInterrupt:
        log.info("Interrompido.")
    except Exception as e:
        tratar_erro(e, "erro fatal")
        sys.exit(2)


if __name__ == "__main__":
    main()
