# dm_followers

> ⚠️ **DM em massa é o sinal de spam nº 1 do Instagram** — risco de bloqueio bem
> maior que follow. Comece com caps minúsculos, varie a mensagem (spintax) e rode
> `--dry-run` primeiro. Viola os ToS; é a sua conta e o seu risco.

Manda DM pros **novos seguidores** lidos da **aba de notificações** ("começou a
seguir você"). Processa do mais antigo pro mais novo, com retomada: na 1ª vez você
escolhe de quem começar; depois ele só pega os novos desde o último.

Mesma base do like-bot: Chrome logado via Playwright, chamadas internas via página
logada. Endpoints em [`../../DM_API_REFERENCE.md`](../../DM_API_REFERENCE.md).

## Setup
```bash
cd projetos/ig-automations-hub/workers/dm_followers
pip install -r requirements.txt
python -m playwright install chromium
```

## Uso
```bash
python main.py --login                              # 1ª vez: login manual
python main.py --dry-run --start-from juliatilco    # simula a partir de um seguidor
python main.py --start-from juliatilco              # 1ª vez pra valer
python main.py                                      # próximos runs: só os novos
python main.py --debug                              # dump do feed em output/
```

### Retomada
Na 1ª vez, `--start-from <username>` define de quem começar (do mais antigo pra
frente). Ao enviar, salva o `timestamp` do último; nos próximos runs só processa
quem é mais novo que o salvo. Quem já recebeu DM nunca recebe de novo (`state.json`).

## Mensagem — `config.py` → `MENSAGEM`
`{username}` vira o nick do destinatário. Suporta **spintax** `{a|b|c}` (escolhe um
aleatório) pra variar o texto e reduzir flag de spam. **Varie bastante.**

## Limites — `config.py` (bem conservadores)
| Parâmetro | Padrão |
|-----------|--------|
| `MAX_DMS_DIA` | 25 |
| `MAX_DMS_HORA` | 8 |
| `MAX_DMS_POR_RUN` | 15 |
| `DELAY_DM` | 40–100 s |
| `PAUSA_LONGA` | 2–5 min (a cada 6 DMs) |
| `ACTIVE_HOURS` | 9–23 |

Kill-switch: qualquer bloqueio (`feedback_required`/`spam`/429/HTML) **para o run** e
imprime o saldo (enviadas / puladas). Erro detalhado vai pra `output/logs/`.

## Arquivos
| Arquivo | Papel |
|---------|-------|
| `config.py` | mensagem, caps, constantes da API |
| `safety.py` | estado/retomada, caps, delays, kill-switch |
| `ig.py` | sessão Playwright + feed de notificações, criar thread, enviar DM |
| `main.py` | retomada + loop de envio + CLI |
