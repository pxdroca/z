"""

app.py
======
Dashboard Streamlit — painel web visual para as apostas capturadas pelo
listener. Lê do mesmo banco Postgres (Neon, via DATABASE_URL) que
listener.py e score_updater.py escrevem — em produção, esses dois rodam
periodicamente via GitHub Actions (ver .github/workflows/), então basta
publicar este app.py no Streamlit Community Cloud apontando pra mesma
DATABASE_URL.

Para rodar localmente: configure DATABASE_URL no seu .env (mesmo banco
Neon) e rode `streamlit run app.py`.

Este painel é compartilhado com os membros do grupo — por isso nenhum dado
de debug (texto cru do OCR/legenda original) é renderizado aqui; ver
_log_debug_info() para onde esse texto vai em vez disso (log de servidor).

Tema visual: a paleta base (fundo escuro, verde-limão de destaque) vem de
.streamlit/config.toml; o CSS injetado abaixo cobre tipografia (Google
Fonts), cores semânticas de green/red, e o card da aposta em si — construído
em HTML/CSS puro (não st.metric/st.columns) para controlar quebra de linha
do mercado e responsividade mobile de verdade. Ver _inject_css() e
_bet_card().
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd
import streamlit as st

import resultado_checker
from config import settings
from database import auto_promote_ao_vivo, init_db, list_bets, update_resultado, update_status
from models import Bet, BetStatus, ResultadoAposta

logger = logging.getLogger("app")

st.set_page_config(page_title="Cansadão Apostas", page_icon="🎾", layout="wide")

init_db()
auto_promote_ao_vivo()  # atualiza 'agendada' -> 'ao_vivo' toda vez que o painel recarrega


STATUS_LABELS = {
    BetStatus.AGENDADA.value: "🟢 Agendada",
    BetStatus.AO_VIVO.value: "🔴 Ao vivo",
    BetStatus.NAO_ENCONTRADA.value: "⚠️ Não encontrada na Superbet",
    BetStatus.ERRO_EXTRACAO.value: "❌ Erro na extração",
    BetStatus.ENCERRADA.value: "⏹️ Encerrada",
}

RESULTADO_LABELS = {
    ResultadoAposta.PENDENTE.value: "⏳ Pendente",
    ResultadoAposta.GREEN.value: "✅ Green",
    ResultadoAposta.RED.value: "❌ Red",
    ResultadoAposta.VOID.value: "➖ Void",
}

# Classe CSS por resultado — ver _inject_css() para as cores reais
# (verde/vermelho semânticos, diferentes do verde-limão de marca).
RESULTADO_CSS_CLASS = {
    ResultadoAposta.GREEN.value: "resultado-green",
    ResultadoAposta.RED.value: "resultado-red",
    ResultadoAposta.VOID.value: "resultado-void",
    ResultadoAposta.PENDENTE.value: "resultado-pendente",
}

STATUS_CSS_CLASS = {
    BetStatus.AGENDADA.value: "status-agendada",
    BetStatus.AO_VIVO.value: "status-ao-vivo",
    BetStatus.NAO_ENCONTRADA.value: "status-alerta",
    BetStatus.ERRO_EXTRACAO.value: "status-alerta",
    BetStatus.ENCERRADA.value: "status-encerrada",
}

# Logo real de cada casa (SVG oficial, embutido como data URI — sem
# depender de servir estáticos nem de rede em tempo de execução) sobre o
# fundo sólido de cor de marca.
LOGO_SUPERBET = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA1MTIgOTAuNzE4MDAyIiBmaWxsPSJub25lIj4KICA8cGF0aCBmaWxsPSIjZmZmZmZmIiBkPSJtIDQ1LjA3NjE2NCwyOC4wNzg5OTcgYyAtMC40MTgwMjUsLTQuODgzNDQ2IC0yLjU2NjQ4MiwtOC4zMDIxODEgLTkuMTU3NjczLC05LjMwOTk3OSAtNi4yOTMwNjYsLTAuOTcyMTUyIC05LjI3NzU3NCwxLjA1OTY0NyAtOS43OTkyOTUsNC4yOTY5MTMgLTAuNTk2MjUzLDMuNzI2NTg0IDEuODk1Njk2LDUuNTQ0NTA5IDguMzkyOTE0LDcuNTUwMzgyIHEgNC4xMzE2NDcsMS4yODMyNDEgOC4yNzMwMTUsMi41NDcwMzkgQyA1OS4xNDk2ODcsMzguMTYwMjE1IDY0LjM2NjkwNCw0NC44ODEwMjcgNjIuODY5NzksNTYuMzI2NDk5IDYxLjIyNjg1Myw2OS4xMDcwNiA0OS41MTg5LDc3LjgwNDU4MiAyOC40MTAyMzQsNzQuNjAyOTYxIDcuNDExNzQ3LDcxLjM3ODY1NSAtMy4xNjUyNjksNjEuMTQ4Mzc0IDAuODMzNTE3MSw0NS4xMzA1NDYgcSA5LjkyNTY3MzksMS43MzA0MzEgMTkuODcwNzkxOSwzLjMyODAwMSBjIC0xLjMzNTA4OSw1LjAxMzA2NSAwLjY5MzQ2OCw5LjM5MDk5IDEwLjQ1NzExNywxMC44ODgxMDQgNy40OTg1MzQsMS4xNDA2NTkgMTEuMDE0NDg0LC0wLjY3NDAyNSAxMS41Njg2MSwtNC41MDQzMDUgMC40ODYwNzYsLTMuMzQ0MjA0IC0xLjQxOTM0MiwtNS4zMTc2NzIgLTcuMDk2NzEsLTcuMDc0MDI3IFEgMzEuNTU2NzY4LDQ2LjUwMTI4MSAyNy40ODM0NDksNDUuMjA4MzE4IEMgMTAuOTgyNzg2LDM5Ljk5MTEwMSA0LjQyMDc1ODgsMzIuMjIzNjA1IDYuNDkxNDQyOSwyMC4zOTg5OTQgOC40Nzc4NzM5LDkuMTIyMDI4MiAxOC43NDA1NjEsMC40Mjc3NDY5NyAzOC41NjU5ODUsMy40OTY1MDc0IDU3LjMxMjMyLDYuMzQ4MTUzOSA2Ni4xMTY3NzgsMTUuOTQ5Nzc4IDY0LjUwMzAwNSwzMC42ODQzNjUgUSA1NC43ODE0ODQsMjkuNDQ2NDkxIDQ1LjA3NjE2NCwyOC4wNzg5OTcgTSA2OS40ODY5MDYsNTAuOTczMTggNzQuNzE3MDg1LDkuOTc0MjgxNiBxIDkuNzIxNTIyLDEuMjQxMTE0NCAxOS40NTYwMDYsMi4zNDkzNjc0IEwgODkuNjAwNzM1LDUyLjQ5OTQ2IGMgLTAuOTE3MDY0LDcuOTkxMDkxIDIuMjM1OTUsMTIuMzYyNTM1IDkuNjU2NzEyLDEzLjE1NjQ1OSA3LjQyMDc2MywwLjc4NzQ0NCAxMS40MTk1NDMsLTIuODI1NzIxIDEyLjIwMzc1MywtMTAuODI5Nzc1IGwgMy45ODU4MywtNDAuMjM3Mzc5IHEgOS43NDc0NCwwLjk2MjQzIDE5LjUwMTM2LDEuNzk4NDgxIEwgMTMxLjQyOTIsNTcuNTY0MzcyIEMgMTI5Ljg5NjQ1LDc2LjA1Nzk0OCAxMTguMDM2MTksODUuNzE3OSA5Ny4zNTIwMjksODMuNTM3MDM5IDc2LjY3NDM1MSw4MS4zMTQwNTEgNjcuMDk1NDExLDY5LjM2OTU0MSA2OS40OTAxNDcsNTAuOTY5OTQgbSA2OC45NjEyMzMsMzQuOTg0NTE4IDUuNDUzNzgsLTY4LjgyODM3NyBxIDE1LjQzNDU0LDEuMjIxNjcxIDMwLjg4ODUxLDIuMTE2MDUxIGMgMTYuMTA4NTcsMC45MzAwMjYgMjYuODIxNjgsMTAuNTQxMzcyIDI2LjI3NDAzLDI1LjY0MjEzNSAtMC41NDQ0LDE0Ljk4MDg2NiAtMTIuMzU2MDUsMjMuOTE4MTg1IC0yOS4wODY3OCwyMi45NTU3NTQgYSAxNDk3LjExNDQsMTQ5Ny4xMTQ0IDAgMCAxIC0xMS43NDM2LC0wLjcyNTg3NCBsIC0xLjMzNTA5LDIwLjMyNDQ2MyBxIC0xMC4yMjcwNSwtMC42NzQwMjUgLTIwLjQ1MDg1LC0xLjQ4NDE1MiBtIDIyLjg2ODI2LC0zNS4yOTU2MDYgcSA0LjY4NTc4LDAuMzExMDg4IDkuMzcxNTUsMC41ODY1MzEgYyA2LjAxNzYzLDAuMzU2NDU2IDkuMzM5MTQsLTIuNTYwMDAxIDkuNTkxOSwtNy4zMjM1NDYgMC4yMzk4MSwtNC42NjYzMzEgLTIuNzE1NTQsLTcuOTk0MzMyIC04Ljk2MzI0LC04LjM2Mzc1IHEgLTQuNDg4MTEsLTAuMjY4OTYyIC04Ljk2OTczLC0wLjU2MDYwOCB6IG0gNDQuMjcxODEsMzkuMTI5MTI2IDIuNDEwOTQsLTY5LjAwMzM2NCBxIDI1LjI5MjE3LDAuODgxNDE4IDUwLjYwMDUyLDAuODg3ODk5IGwgLTAuMDAzLDE2Ljg4OTUyNSBxIC0xNS43MDAyNSwwIC0zMS40MDA1MSwtMC4zNDAyNTQgbCAtMC4yMDA5Miw5LjIzNTQ0NiBxIDEzLjQwOTIyLDAuMjkxNjQ2IDI2LjgyNDkyLDAuMzMzNzczIGwgLTAuMDU4MywxNi4zOTM3MjcgcSAtMTMuNTYxNTIsLTAuMDQ4NjEgLTI3LjExOTgsLTAuMzM3MDEzIGwgLTAuMjA3MzksOS42MzQwMjggcSAxNi4yODM1NSwwLjM0OTk3NSAzMi41NjcxLDAuMzQ2NzM0IHYgMTYuODg5NTI1IHEgLTI2LjcxMTUxLDAgLTUzLjQxNjUzLC0wLjkzMDAyNiBtIDEyMy45ODgzLC0xOC4yMTQ4OTIgMC44MDY4OCwxNy4xNjgyMDggYyAtMS42NDk0MSwwLjU3NjgxIC00LjA0MDkxLDAuOTgxODczIC02LjMzODQyLDEuMDc5MDg5IC0xMi43NDQ5MiwwLjU0NzY0NiAtMjMuMjY2ODUsLTIuMjQ1NjcyIC0zMC42ODc2MiwtMTkuNjYzMzk5IGwgLTAuODY4NDUsLTEuOTcwMjI4IHEgLTIuODg0MDUsMC4wNjQ4MSAtNS43NjgxLDAuMTE5ODk4IGwgMC40MDgzLDIyLjE0ODg2OCBxIC0xMC4yNCwwLjE5NDQzIC0yMC40ODk3MywwLjI0MzAzOSBMIDI2Ni4yODYxNiwyMS42NTMwNyBxIDE2LjAyMTA3LC0wLjA4MTAxIDMyLjAzODksLTAuNTE4NDgxIGMgMTYuMjI4NDUsLTAuNDQwNzA5IDI3Ljc2NzksNy4xMjkxMTYgMjguNTI5NDIsMjIuODE2NDEyIDAuNTAyMjgsMTAuNjE1OTAzIC00LjcxNDk0LDE3LjU0MDg2NiAtMTQuNTAxMjcsMjAuMzgyNzkyIDQuNzA4NDYsNy43ODM2OTggOS43OTkyOSw3Ljc3NzIxNyAxMy45Mjc3LDcuNTg5MjY3IGEgMjkuMTY0NTY2LDI5LjE2NDU2NiAwIDAgMCAzLjI5NTYsLTAuMzQ5OTc0IG0gLTQzLjQyNjA0LC0zMy45NjA1MTcgMC4yNzg2OCwxNS4wMDAzMDggcSA1LjQ1Mzc3LC0wLjEwMzY5NiAxMC45MDc1NSwtMC4yNDMwMzggYyA2LjAxNzYyLC0wLjE1ODc4NSA4Ljg3MjUsLTMuMzM3NzIyIDguNzI2NjgsLTcuODA2MzgyIC0wLjE0MjU4LC00LjM3MTQ0NSAtMy4xNjU5NywtNy4zNDYyMyAtOS40MTY5MSwtNy4xODA5NjQgcSAtNS4yNDk2MiwwLjEyOTYyIC0xMC40OTYsMC4yMzAwNzYgbSA1MS4xMzg0NSw1MS4wODY1OTggLTMuNTUxNiwtNjguOTU0NzU2IHEgMTUuNDU3MjIsLTAuNzkzOTI0IDMwLjg5ODI0LC0xLjkyMTYyMSBjIDE2LjQwNjY5LC0xLjIwODcwOSAyNS40MjUwMiw1LjI5MTc0OSAyNi40NTg3NCwxNi40ODQ0NjEgMC43Mzg4Myw4LjExNDIzMSAtNC40ODgxLDEzLjc3MjE1NyAtMTAuNTcwNTMsMTUuOTY1OTggOC40MjIwNywxLjUwMDM1NSAxNC4zNzE2NSw1LjczNTY5OCAxNS4xNzUyOSwxNC40Mzk3MDEgMS4xOTI1LDEyLjc0ODE1NiAtNy4zNDYyMywyMC41NzcyMjEgLTIzLjc1MjkxLDIxLjgwODYxNCBxIC0xNy4zMTcyOCwxLjI4NjQ4MiAtMzQuNjU3MjMsMi4xNzc2MjEgTSAzNTUuMDQ2OSw0NS44NjkzODEgcSA0LjkyNTU3LC0wLjMyMDgwOSA5Ljg0NzksLTAuNjcwNzg0IGMgNC44NzM3MiwtMC4zNTMyMTYgNy4xMTI5MSwtMy4xMjA2MDkgNi43OTg1OCwtNy4xODA5NjUgLTAuMzE0MzMsLTQuMDYwMzU2IC0yLjkxOTY5LC02LjM0ODE1MyAtNy43NDQ4MSwtNS45OTgxNzggcSAtNC44NzY5NiwwLjM0NjczMyAtOS43NTcxNywwLjY2NDMwMyB6IG0gMS43OTE5OSwyNy41NjA1MTYgYSAxNTI5LjUxOTUsMTUyOS41MTk1IDAgMCAwIDkuOTI1NjgsLTAuNjc3MjY3IGMgNS42OTAzMywtMC40MDgzMDQgOC4zOTI5MSwtMy4xMTczNjggOC4wNTI2NiwtNy40NzI2MSAtMC4zNDAyNSwtNC4zNjE3MjIgLTMuMzk5MywtNi42MDczOTQgLTkuMDMxMywtNi4yMDIzMyBxIC00LjkxNTg0LDAuMzQ5OTc1IC05LjgzNDkzLDAuNjcwNzg0IHogbSA0Ny4yMjM5MiwxMC4zNTk5MDIgLTYuNTc4MjQsLTY4LjczMTE2MSBhIDE0NTQuOTg3OCwxNDU0Ljk4NzggMCAwIDAgNDkuODY4MTgsLTUuNjQxNzIzNiBsIDIuMTg3MzQsMTYuNzQ2OTQxNiBxIC0xNS4zNTM1MiwyLjAwMjYzNCAtMzAuNzMyOTgsMy42ODQ0NTggbCAxLjAwNDU3LDkuMTgzNTk3IHEgMTMuMzUwODksLTEuNDU4MjI5IDI2LjY4MjM0LC0zLjE1OTQ5NCBsIDIuMDc3MTYsMTYuMjYwODY1IHEgLTEzLjQ4MDUxLDEuNzE3NDY5IC0yNi45ODA0NywzLjE5NTE0IGwgMS4wNDY2OSw5LjU3ODk0IHEgMTYuMjEyMjYsLTEuNzY5MzE3IDMyLjM5MjExLC0zLjg5NTA5IGwgMi4yMDAzLDE2Ljc0Njk0MyBhIDE1MjYuMjc5LDE1MjYuMjc5IDAgMCAxIC01My4xNjcsNi4wMjczNDMgbSA3Ny41NTgzLC05LjQzMzExNyAtNy42MjE2NywtNTEuNTk4NTk5IHEgLTkuMDY2OTQsMS4zNDE1NyAtMTguMTQwMzYsMi41NjY0ODIgTCA0NTMuNjAwNDQsOC41ODQxMDM5IEEgMTQ1NC45ODc4LDE0NTQuOTg3OCAwIDAgMCA1MDkuMDk3MzcsMCBsIDIuOTAzNSwxNi42MzY3NjQgcSAtOS4xODY4NCwxLjYwNDA1MiAtMTguMzg2NjQsMy4wOTE0NDQgbCA4LjMxODM4LDUxLjQ5MTY2MyBxIC0xMC4xNDkyNywxLjYzNjQ1NiAtMjAuMzExNSwzLjEzNjgxMSIgLz4KPC9zdmc+Cg=="
LOGO_BETANO = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxODcuNTY4NDIgNDAuNjI0Mjk4Ij4KICA8ZyB0cmFuc2Zvcm09InRyYW5zbGF0ZSgtMTg5LjM0NzkyLC0xMDcuMjQxNzkpIj4KICAgIDxwYXRoIGZpbGw9IiNmZmZmZmYiIGQ9Im0gMjUwLjkxNjU2LDExNS42MTMyNSBjIC0xMC44MzEyOCwwIC0xOC42NjMyNCw5LjAwNjk2IC0xOC42NjMyNCwxOC44MDI2MyAwLDguNDU4NDYgNS44ODMzNSwxMy40NDg2IDEzLjY3MTMsMTMuNDQ4NiA3Ljc4Nzk1LDAgMTIuNjI3NDksLTQuODQzNjQgMTQuMTQ3NzEsLTEwLjgwNjY3IC0yLjA5MzMyLDEuNTAyNiAtNS42ODc1NSwzLjE5OTExIC05Ljc0NDE4LDMuMTk5MTEgLTMuNDY4MjIsMCAtNy42Mjk2NSwtMS40ODMyMSAtOC4wMzUxNSwtNi4wNzEzNCA4LjcxMTQ2LC0xLjEzNTEgMTUuODEzNzEsLTMuOTI1OTMgMTkuNjc2NDMsLTYuODQ5MTUgMC4wNiwtMC40ODcgMC4xMjIwMSwtMS4xOTYxMSAwLjEyMjAxLC0yLjExMDMxIDAsLTQuOTI5MjQgLTMuMDg1NjMsLTkuNjE0NjcgLTExLjE3NjU4LC05LjYxNDY3IHogbSAxLjI3ODIxLDEyLjQxMjM5IGggLTkuNDk0NDcgYyAwLjg1MywtNC4zODA2MyAzLjk1NTczLC03LjYwNTA2IDYuOTk5NjQsLTcuNjA1MDYgMi4wNjgyMywwIDMuMDQxNjMsMS4wOTUzMSAzLjA0MTYzLDMuNzcyOTMgMCwxLjIxNzIxIC0wLjE4MSwyLjQ5NTQyIC0wLjU0NywzLjgzMzkzIHogbSAxMTAuNTQ2MDgsLTEyLjQxMjM5IGMgLTExLjEzNjU4LDAgLTE5LjA0NjQ0LDguNjQxMjYgLTE5LjA0NjQ0LDE5LjcxNjk0IDAsOC4wOTI2NiA2LjMyOTM1LDEyLjUzNDI5IDE0LjE3ODIsMTIuNTM0MjkgMTEuMTM0MjksMCAxOS4wNDQxNCwtOC42NDEyNiAxOS4wNDQxNCwtMTkuNzE2OTQgMCwtOC4wOTI2NiAtNi4zMjY5NCwtMTIuNTM0MjkgLTE0LjE3ODIsLTEyLjUzNDI5IHogbSAtNC41MDI0MywyNy4zMjE5OSBjIC0yLjU1NjQyLDAgLTMuOTU1OTMsLTIuMDY4NzIgLTMuOTU1OTMsLTUuOTYzNjMgMCwtNy43Mjg3NiAzLjEwMjYyLC0xNi40MzA5MyA4LjA5Mjc2LC0xNi40MzA5MyAyLjU1NTcyLDAgMy45NTU4MiwyLjA2ODIyIDMuOTU1ODIsNS45NjMwNCAwLDcuNzI5MzYgLTMuMTY1OTIsMTYuNDI5ODIgLTguMDkyNjUsMTYuNDI5ODIgeiBtIC0xNC45NzI0MSwtMTkuNDczMDMgYyAwLDAuOTczIC0wLjEsMi4wNjExMSAtMC4zMjYsMy4yMjQ5MiBsIC0zLjk5MzgzLDIwLjU2Nzg1IGggLTkuOTI1MjcgbCAzLjUyMjEzLC0xOC4xMzQwMyBjIDAuMTgsLTAuOTEyMDEgMC4yNTUsLTEuNzAyNDIgMC4yNTUsLTIuNDMzODIgMCwtMi44NTkyMiAtMS41MTk2MiwtNC4xMzY4NCAtNC4yNTg3MywtNC4xMzY4NCAtMS4wOTQ4MiwwIC0yLjQ1MzMyLDAuMzUyMDEgLTMuMzIzNTMsMC43MjkwMiBsIC00LjY1NDkzLDIzLjk3NTY3IGggLTkuOTIxMTcgbCA2LjAyODA0LC0zMS4wMzQwMyBoIDkuOTIxMTcgbCAtMS4wOTQ4LDUuNTk3OTQgYyAxLjYwOTMsLTIuNTc3NTEgNS4xOTUzMywtNi4yMDc0NCA5Ljg2Mzc2LC02LjIwNzQ0IDQuMzE5NzMsMCA3LjkwODA2LDIuNjE2NzEgNy45MDgwNiw3Ljg0ODk2IHogbSAtMzEuNzQ0MjIsMi4wMjEyMSBjIDAsLTUuNTk3OTQgLTQuODc3NjQsLTkuODcwMTcgLTExLjcwNTU5LC05Ljg3MDE3IC04LjQ5NjU2LDAgLTEyLjkzMjg4LDUuNTkwMjQgLTE0LjI0MSwxMC44MDY2NyAyLjY2NDIyLC0xLjk0MDQxIDYuNDU4MjUsLTMuMTk5MjEgOS43NDQxNywtMy4xOTkyMSAzLjA2MjEyLDAgNi4wOTg0NSwwLjk5NiA2LjA5ODQ1LDQuMDc3NjIgMCwwLjMwNSAwLDAuNjY4MDEgLTAuMDYsMS4xNTU3MSAtNy44Mjc3NiwwLjMxNjAxIC0xOS4xMDczNSw0LjUwNTA0IC0xOS4xMDczNSwxMy4xNDM5IDAsMy41OTA2MiAyLjU5NzkzLDYuMjY4MjQgNi42MTI4Niw2LjI2ODI0IDQuMDE1MDMsMCA3Ljc0NjM1LC0yLjczODUyIDkuOTM4MTYsLTYuMjA3MzQgbCAtMS4wODU0LDUuNTk3ODQgaCA5LjkxOTM3IGwgMy42MjA3MywtMTguNjY5MDQgYyAwLjE4MSwtMS4wOTUzMSAwLjI2NywtMi4xMjkwMiAwLjI2NywtMy4xMDI0MiB6IG0gLTEyLjQzMzU5LDE0LjcxNDkgYyAtMS4yMTcyMSwwLjU0NiAtMi43ODA3MiwwLjkxMjAxIC0zLjkzNjQzLDAuOTEyMDEgLTIuNDk1MzIsMCAtMy43MjE5MiwtMS4zNzMyMSAtMy43MjE5MiwtMy4xOTk4MiAwLC0zLjg1NDQzIDQuNTY3NjMsLTYuMjk4MjUgOC45NzY0NSwtNC40OTY3MyBsIC0xLjMxOTgsNi43ODQ1NCB6IG0gLTIyLjMwMzA2LDAuMDYgYyAxLjMyMjExLDAgMi42MDU1MiwtMC4zNzUgNC4yNzU4MywtMS42MzM5MSAtMS41MzE0MSw1LjIyMjI0IC01LjgxNDc0LDkuMjQxMzcgLTEwLjQwODY3LDkuMjQxMzcgLTUuNjM5NTQsMCAtNy40MTgyNiwtNC43NDA0MyAtNi4yNDQ5NCwtMTAuODA2NjcgbCAyLjk4NjQxLC0xNS4zNTkwMiBoIC00LjA1NDgzIGwgMS4wNjQzMiwtNS40Nzc3NCBoIDQuMDY2NTIgbCAxLjExODExLC01Ljg0NzAzIDEwLjIxNTk3LC0xLjQzNjQyIC0xLjQyODIsNy4yODM0NSBoIDguNTA1ODYgYyAtMC42MzgwMiwzLjE4NDUzIC0yLjYxODQzLDUuNDc3NzQgLTYuNjgxNDYsNS40Nzc3NCBoIC0yLjg4NzUyIGwgLTIuOTIxMzEsMTUuMDM0OTEgYyAtMC40NTEwMSwyLjMwNDMyIDAuNzksMy41MjMzMiAyLjM5MzkxLDMuNTIzMzIgeiBtIC00Ni4xMDg4MywtOC40MjM3NiBjIDAsOC4zMzY0NiAtMTEuMjE5NzcsMTUuNDIxNzIgLTI5Ljk1MzksMTUuNDIxNzIgaCAtMTEuMzc0NDkgbCA3Ljc3Mjc2LC00MC4wMTQ0OSBoIDI0Ljk3MzU3IGMgNy4wMzk2NSwwIDEwLjgwODU4LDEuNDUxNjEgMTAuODA4NTgsNS44NDM0NCAwLDUuMjU4NTMgLTguMzk5MjYsMTEuMDUxMDggLTIxLjYxNTc1LDEzLjQ0MjY5IGwgNy4yMDk1NCwtMTEuOTk2ODcgLTExLjAxNDc3LC0wLjAzIC00Ljc5NTYzLDE0Ljc3MjQgNy41OTU3NSwtMS4wNjY2MSAtOS42NTY4NywxNi4wNzI5MSAyMi43NjIwNywtMTguOTE2MzMgYyA0LjQzNjMyLDAgNy4yODkxNCwyLjU3NTAyIDcuMjg5MTQsNi40NzE3NCB6IiAvPgogIDwvZz4KPC9zdmc+Cg=="
LOGO_BET365 = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjE3Ljk3MyAxNy4yNzYgNjMuMTggMTYuNDE0Ij4KICA8cGF0aCBmaWxsPSIjZmZmZmZmIiBkPSJNMTguOTc0LDE4LjI3N2MxLjQxOS0wLjAwMSwyLjgzOC0wLjAwMiw0LjI1OCwwYy0wLjAxMywxLjg2OCwwLjAwMiwzLjczNS0wLjAwOSw1LjYwMwogICAgYzAuNDA0LTAuNTc1LDAuOTMxLTEuMDk3LDEuNjE5LTEuMzFjMS4yMzQtMC4zODgsMi43MjYtMC4xMTgsMy42MSwwLjg3MWMwLjg4OSwwLjk5MSwxLjE5MSwyLjM1NywxLjIyOCwzLjY1NAogICAgYzAuMDIxLDEuMzAxLTAuMTA0LDIuNjY0LTAuNzUsMy44MmMtMC40NCwwLjgxMy0xLjIwOSwxLjQ1Ni0yLjEyLDEuNjU4Yy0wLjkwOSwwLjIwMy0xLjk0NywwLjE3NC0yLjczOC0wLjM3CiAgICBjLTAuNTQzLTAuMzY1LTAuODQzLTAuOTctMS4xMi0xLjU0MmMwLjAxOCwwLjYwNC0wLjAxNiwxLjIwNi0wLjAyOCwxLjgwOWMtMS4zMTcsMC0yLjYzNCwwLTMuOTUxLDAKICAgIEMxOC45NzMsMjcuNzM5LDE4Ljk3MywyMy4wMDgsMTguOTc0LDE4LjI3N0wxOC45NzQsMTguMjc3eiIvPgogIDxwYXRoIGZpbGw9IiNmZmZmZmYiIGQ9Ik00Mi4zNDcsMjAuMzQ3YzEuNDIyLTAuNDU4LDIuODI1LTAuOTc0LDQuMjQ3LTEuNDMxYy0wLjAxMiwxLjIzOS0wLjAwNCwyLjQ3OS0wLjAwNSwzLjcxOAogICAgYzAuNjQ2LDAsMS4yOTMtMC4wMDEsMS45NDEtMC4wMDFjLTAuMDA4LDAuOTM4LTAuMDAxLDEuODc1LTAuMDA2LDIuODEyYy0wLjY0NywwLjAxMS0xLjI5Ni0wLjAxNy0xLjk0MiwwLjAxNQogICAgYzAuMDIsMC45NzMtMC4wMTEsMS45NDYsMC4wMTUsMi45MTljMC4wMTEsMC40MzMsMC4xOTgsMC45MjMsMC42MjgsMS4wOTVjMC40MzQsMC4wOTQsMC44ODYsMC4wMDcsMS4zMDYtMC4xMTkKICAgIGMtMC4wMDksMC45NC0wLjAwNSwxLjg4MS0wLjAwNCwyLjgyMWMtMS4xODcsMC4zNjktMi40NDgsMC42My0zLjY5MiwwLjQ1Yy0wLjc1Mi0wLjExLTEuNDcyLTAuNTEzLTEuODg1LTEuMTYxCiAgICBjLTAuNDYyLTAuNzAxLTAuNTg3LTEuNTYxLTAuNjE4LTIuMzgyYy0wLjAwMy0xLjIxMywwLTIuNDI1LTAuMDAxLTMuNjM3Yy0wLjQ5NiwwLjAwMS0wLjk5MS0wLjAwNi0xLjQ4NiwwLjAxCiAgICBjLTAuMDE4LTAuOTQxLTAuMDA2LTEuODgyLTAuMDA2LTIuODI0YzAuNDk3LDAuMDAzLDAuOTk0LDAuMDAxLDEuNDkyLDAuMDAxQzQyLjM0MSwyMS44NzEsNDIuMzEsMjEuMTA3LDQyLjM0NywyMC4zNDcKICAgIEw0Mi4zNDcsMjAuMzQ3eiIvPgogIDxwYXRoIGZpbGw9IiNmZmZmZmYiIGQ9Ik00OS41NDIsMTkuNTkxYzEuMzgyLTAuNTEsMi44ODMtMC41MzMsNC4zMzktMC41MTZjMS4zMiwwLjA1NiwyLjY4NiwwLjQzOSwzLjY5LDEuMzMxCiAgICBjMS4zMjgsMS4xNjMsMS4zODMsMy41NS0wLjAxNCw0LjY3N2MtMC4zNTcsMC4zMDEtMC43ODEsMC41MS0xLjIyMywwLjY1NGMwLjY0MSwwLjIyNSwxLjI4LDAuNTMyLDEuNzIyLDEuMDY3CiAgICBjMC43MDksMC44MTksMC44MjIsMi4wMDMsMC41NjYsMy4wMjJjLTAuMjMxLDAuOTM0LTAuOTMzLDEuNjkyLTEuNzc4LDIuMTIyYy0xLjUzNCwwLjgzMi0zLjM0OCwwLjgxNS01LjAzOCwwLjY1MQogICAgYy0wLjc5My0wLjA3NS0xLjU3NC0wLjIzNC0yLjM0Mi0wLjQ0MWMtMC4wMzItMS4wMjEsMC0yLjA0NC0wLjAxNi0zLjA2NGMxLjMxOCwwLjQzNywyLjc2MywwLjcyNyw0LjEzOCwwLjM5NwogICAgYzAuOTIyLTAuMjMyLDAuOTY5LTEuNjY1LDAuMTQ2LTIuMDU2Yy0wLjg3LTAuNDMzLTEuODg5LTAuMjY2LTIuODE0LTAuMTQzYzAuMDA1LTAuOTY0LDAuMDAxLTEuOTI5LDAuMDAyLTIuODk1CiAgICBjMC43MTgsMC4wMzQsMS40NDMsMC4wNzUsMi4xNTUtMC4wNDRjMC40My0wLjA3NiwwLjkxNy0wLjI5MiwxLjAzNS0wLjc1NGMwLjA2Ni0wLjM3LDAuMDI2LTAuNzkxLTAuMjMtMS4wODMKICAgIGMtMC4yOTEtMC4zNC0wLjc1MS0wLjQ3NC0xLjE4NC0wLjVjLTEuMDY0LTAuMDU1LTIuMTM1LDAuMTMtMy4xMzEsMC41QzQ5LjUzLDIxLjU0Myw0OS41NywyMC41NjYsNDkuNTQyLDE5LjU5MUw0OS41NDIsMTkuNTkxeiIvPgogIDxwYXRoIGZpbGw9IiNmZmZmZmYiIGQ9Ik02MC44NjYsMjEuMTI0YzAuOTkxLTEuMjI0LDIuNTI0LTEuOTI3LDQuMDgyLTIuMDMyYzEuNDQ4LTAuMDU5LDIuOTIzLTAuMDM4LDQuMzMyLDAuMzQ2CiAgICBjLTAuMDIsMS4wMDcsMCwyLjAxNS0wLjAxMSwzLjAyMmMtMS4xNDctMC4zMjEtMi4zNDMtMC41NDgtMy41MzgtMC40MTRjLTAuNzI2LDAuMDk4LTEuNDQ4LDAuNDcyLTEuODE3LDEuMTI1CiAgICBjLTAuMjU4LDAuNDM4LTAuMzE2LDAuOTUyLTAuMzU0LDEuNDQ5YzAuODYyLTAuNjc0LDIuMDA5LTAuODMzLDMuMDczLTAuNzY4YzEuMzQ3LDAuMDg3LDIuNjI1LDAuOTA0LDMuMjQ1LDIuMTA2CiAgICBjMC40NjQsMC44OTYsMC41NDUsMS45NDgsMC40MDIsMi45MzZjLTAuMTUyLDEuMDQ3LTAuNzE0LDIuMDM3LTEuNTY3LDIuNjdjLTEuMjU1LDAuOTc5LTIuOTM0LDEuMjY3LTQuNDg1LDEuMDY2CiAgICBjLTEuMzU0LTAuMTY1LTIuNjIyLTAuODg0LTMuNDYyLTEuOTU2Yy0wLjk4NC0xLjE4OS0xLjQyNC0yLjc0OC0xLjQ1My00LjI3NUM1OS4yNzMsMjQuNTQ3LDU5LjY0MiwyMi41NzYsNjAuODY2LDIxLjEyNAogICAgTDYwLjg2NiwyMS4xMjR6Ii8+CiAgPHBhdGggZmlsbD0iI2ZmZmZmZiIgZD0iTTcxLjI2NSwxOS4yOThjMi43ODEtMC4wMDEsNS41NjItMC4wMDEsOC4zNDIsMGMtMC4wMDMsMS4wMTIsMCwyLjAyNC0wLjAwMiwzLjAzNgogICAgYy0xLjQ4OCwwLjAxMi0yLjk3OC0wLjAyMS00LjQ2NSwwLjAxN2MwLjAxNCwwLjY3LDAuMDE3LDEuMzQzLTAuMDAxLDIuMDE0YzAuODQ0LTAuMDE2LDEuNzE5LTAuMTQzLDIuNTMzLDAuMTQ5CiAgICBjMS4xNjQsMC4zOSwyLjA2OCwxLjQzMSwyLjMzOSwyLjYyNGMwLjI1OCwxLjE0OSwwLjE5LDIuNDI1LTAuNDEsMy40NmMtMC41NjQsMC45Ny0xLjYwMywxLjU2My0yLjY2NSwxLjgzOAogICAgYy0xLjkxNywwLjQ3OC0zLjkyOSwwLjIxOS01LjgwOC0wLjI5OWMtMC4wMjgtMS4wMDUtMC4wMDUtMi4wMS0wLjAxMy0zLjAxNWMxLjIwNiwwLjMwNCwyLjUwNiwwLjYwMSwzLjcyNywwLjIwOAogICAgYzEuMDE2LTAuMzE1LDEuMTY3LTEuODM1LDAuMzY2LTIuNDUzYy0wLjQ4MS0wLjM2Mi0xLjEwNC0wLjQ1LTEuNjktMC40NzVjLTAuNjU2LTAuMDEtMS4zMTMsMC4wNzctMS45NTEsMC4yMjgKICAgIGMtMC4xMzYsMC4wMjYtMC4yNTQsMC4xMDQtMC4zNjIsMC4xODVjMC4wMzEtMC4xMDksMC4wNTItMC4yMiwwLjA2MS0wLjMzMkM3MS4yNjMsMjQuMDg4LDcxLjI2NCwyMS42OTIsNzEuMjY1LDE5LjI5OAogICAgTDcxLjI2NSwxOS4yOTh6Ii8+CiAgPHBhdGggZmlsbD0iI2ZmZmZmZiIgZD0iTTMxLjY4LDIzLjgwOGMxLjA4My0xLjA4NywyLjY5My0xLjQwNiw0LjE3Ni0xLjM5OWMxLjEyMi0wLjAzMiwyLjMwOCwwLjI1LDMuMTUxLDEuMDI4CiAgICBjMC44NzQsMC44MDcsMS4yNzksMS45OTMsMS40MzgsMy4xNDVjMC4xMDgsMC42OTYsMC4wOTUsMS40MDIsMC4wOTMsMi4xMDRjLTEuOTcxLDAtMy45NDEsMC01LjkxMywwCiAgICBjMC4wMzIsMC4zNDcsMC4xMDMsMC43MTUsMC4zNjgsMC45NjRjMC40NywwLjQ0NSwxLjE1NSwwLjUwNSwxLjc3LDAuNTQ2YzEuMDc3LDAuMDE4LDIuMTk0LTAuMDk4LDMuMTU1LTAuNjIyCiAgICBjLTAuMDA4LDAuODQ4LDAsMS42OTctMC4wMDcsMi41NDZjLTEuMjA0LDAuNDIzLTIuNDg3LDAuNTQ1LTMuNzU1LDAuNTY4Yy0xLjQzOCwwLjAyNi0yLjk4My0wLjIyOC00LjEwNy0xLjE5MgogICAgYy0xLjA0Ny0wLjg4NS0xLjUxMS0yLjI3NC0xLjU3Mi0zLjYwOUMzMC4zODQsMjYuNDU0LDMwLjYzMywyNC44NywzMS42OCwyMy44MDhMMzEuNjgsMjMuODA4eiIvPgogIDxwYXRoIGZpbGw9IiNmZmZmZmYiIGQ9Ik0zNS4wMDYsMjUuMDVjMC4zNC0wLjQ5MiwxLjE3OS0wLjUyMywxLjUxNy0wLjAxMmMwLjMwNCwwLjQ2MywwLjMyMywxLjA0MywwLjMzMywxLjU3OAogICAgYy0wLjc0MSwwLjAwMS0xLjQ4MSwwLjAwMS0yLjIyMiwwLjAwMUMzNC42NTQsMjYuMDgxLDM0LjY4NCwyNS41MDQsMzUuMDA2LDI1LjA1TDM1LjAwNiwyNS4wNXoiLz4KICA8cGF0aCBmaWxsPSIjZmZmZmZmIiBkPSJNMjMuOTM4LDI1LjM1OGMwLjM5OS0wLjEyOSwwLjg3MiwwLjA2OSwxLjA1MywwLjQ1MWMwLjI2MSwwLjUxNiwwLjI1MiwxLjExNSwwLjI2LDEuNjgKICAgIGMtMC4wMiwwLjU2Ni0wLjA0OCwxLjE3OC0wLjM3MSwxLjY2NmMtMC4zMTMsMC40ODMtMS4xMzUsMC40ODQtMS40MzctMC4wMTJjLTAuMjg3LTAuNDYxLTAuMzE2LTEuMDI5LTAuMzE5LTEuNTU3CiAgICBjMC0wLjUxLTAuMDAxLTEuMDMyLDAuMTUtMS41MjRDMjMuMzcxLDI1Ljc0MywyMy42LDI1LjQzOCwyMy45MzgsMjUuMzU4TDIzLjkzOCwyNS4zNTh6Ii8+CiAgPHBhdGggZmlsbD0iI2ZmZmZmZiIgZD0iTTY0LjQ2NywyNi40MTVjMC42MDEtMC4yMiwxLjM2Ny0wLjAzNywxLjcwMiwwLjUzNGMwLjQwOSwwLjY5OCwwLjM4MSwxLjYzOS0wLjA1MywyLjMxOAogICAgYy0wLjQyOCwwLjcxNi0xLjU4NCwwLjgtMi4xMTQsMC4xNmMtMC40NTctMC41MjEtMC41LTEuMjc4LTAuMzU5LTEuOTI5QzYzLjcyOSwyNy4wNCw2NC4wMDksMjYuNTc5LDY0LjQ2NywyNi40MTVMNjQuNDY3LDI2LjQxNXoiLz4KPC9zdmc+Cg=="

# Fundo do botão = cor de marca sólida; logo real (recolorida para branco)
# por cima — mesmo tratamento pras 3 casas.
BOOKMAKER_BADGES = {
    "superbet": {"logo": LOGO_SUPERBET, "cor": "#e2001a"},
    "betano": {"logo": LOGO_BETANO, "cor": "#ff5000"},
    "bet365": {"logo": LOGO_BET365, "cor": "#027b5b"},
}


def _sugerir_resultado(bet: Bet) -> str | None:
    """
    Sugestão de green/red pra apostas que por algum motivo ainda estão
    "pendente" apesar do jogo já ter terminado — normalmente o
    score_updater.py já grava isso sozinho assim que a partida encerra (ver
    resultado_checker.checar_resultado), então esta função só serve de
    fallback (ex: aposta processada antes dessa gravação automática existir).

    Mesma lógica central de resultado_checker.py (vencedor de partida,
    vencedor de set específico/genérico, vencer sem perder set) — só uma
    SUGESTÃO exibida no card, quem confirma é sempre o usuário
    (novo_resultado no selectbox), nunca grava sozinho aqui. Mercados que
    resultado_checker.py não sabe interpretar (aces, games, dupla falta,
    placar exato) não sugerem nada (None).
    """
    return resultado_checker.checar_resultado_de_bet(bet)


def _log_debug_info(bet: Bet) -> None:
    """O texto cru do OCR/legenda é útil só para depuração — nunca deve
    aparecer no painel (compartilhado com o grupo). Vai pro log do
    servidor, não pra tela."""
    if bet.fonte_texto:
        logger.debug("Aposta #%s — texto original: %r", bet.id, bet.fonte_texto)


def _inject_css() -> None:
    """CSS além do que .streamlit/config.toml alcança: tipografia (Google
    Fonts), cores semânticas de green/red (independentes do verde-limão de
    marca, que não tem significado de "ganhou"), o card da aposta (HTML
    puro, para controlar quebra de linha do mercado) e responsividade
    mobile via media query. Testado no dark mode (tema base do projeto);
    usa apenas cores fixas (não depende de prefers-color-scheme) para ficar
    previsível também em light mode do sistema."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800&family=Work+Sans:wght@400;500;600&family=JetBrains+Mono:wght@500;700&display=swap');

        :root {
            --lime: #d7f24d;
            --green: #22c55e;
            --red: #ef4444;
            --muted: #9aa0ac;
            --card-bg: #1c1f26;
            --card-border: rgba(255,255,255,0.08);
        }

        html, body, [class*="css"] {
            font-family: 'Work Sans', sans-serif;
        }

        h1, h2, h3 {
            font-family: 'Archivo', sans-serif !important;
            font-weight: 800 !important;
            letter-spacing: -0.01em;
        }

        [data-testid="stMetricValue"] {
            font-family: 'JetBrains Mono', monospace !important;
            font-weight: 700 !important;
        }

        [data-testid="stMetricLabel"] {
            font-family: 'Work Sans', sans-serif !important;
            color: var(--muted) !important;
            text-transform: uppercase;
            font-size: 0.72rem !important;
            letter-spacing: 0.04em;
        }

        .stLinkButton a {
            font-family: 'Work Sans', sans-serif !important;
            font-weight: 600 !important;
            border-radius: 10px !important;
        }

        button[kind="primary"] {
            background-color: var(--lime) !important;
            color: #14161a !important;
            font-weight: 700 !important;
            border: none !important;
        }

        /* ---------------------------------------------------------------
           Header nativa do Streamlit (barra vazia com Deploy/menu ⋮) —
           some por completo; o app tem seu próprio cabeçalho (.app-header).
           Idem para a badge "Hosted with Streamlit"/"Manage app" (canto
           inferior direito, id="ViewerBadge_container" no HTML real — o
           seletor data-testid usado antes não pegava esse elemento) e o
           menu de 3 pontos (⋮) do topo — só aparecem pro dono do app,
           nunca pros visitantes, mas não têm função pro uso deste painel.
           [class*=...] cobre variações de nome de classe que o Streamlit
           muda entre versões, então é mais robusto que um único seletor.
        --------------------------------------------------------------- */
        header[data-testid="stHeader"],
        div[data-testid="stToolbar"],
        div[data-testid="stDecoration"],
        div[data-testid="stStatusWidget"],
        #MainMenu,
        footer,
        .stAppDeployButton,
        [data-testid="stAppViewerBadge"],
        #ViewerBadge_container,
        div[class*="viewerBadge"] {
            display: none !important;
        }
        /* Botão de recolher/expandir a sidebar (a seta "‹"/"›" no canto
           superior esquerdo) — mantém a função, só reposiciona pra não
           flutuar solto sobre o conteúdo com um espaço vazio enorme acima
           do header (era o que causava o espaçamento excessivo no topo). */
        div[data-testid="stSidebarCollapsedControl"],
        button[data-testid="stBaseButton-headerNoPadding"] {
            top: 0.6rem !important;
        }
        div[data-testid="stAppViewContainer"] > div:first-child {
            padding-top: 0.5rem;
        }
        .main .block-container {
            padding-top: 2.5rem !important;
        }

        /* ---------------------------------------------------------------
           App header
        --------------------------------------------------------------- */
        .app-header {
            font-family: 'Archivo', sans-serif;
            font-weight: 800;
            font-size: 2.1rem;
            letter-spacing: -0.02em;
            margin-bottom: 1rem;
        }

        /* ---------------------------------------------------------------
           Card da aposta — HTML puro (não st.metric/st.columns) para
           controlar quebra de linha do mercado e ser 100% responsivo.
        --------------------------------------------------------------- */
        .bet-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-bottom: none;
            border-radius: 16px 16px 0 0;
            padding: 1.1rem 1.3rem;
            box-shadow: 0 2px 10px rgba(0,0,0,0.25);
        }

        .bet-card .card-header-row {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 0.75rem;
        }

        .bet-card .jogo {
            font-family: 'Archivo', sans-serif;
            font-weight: 700;
            font-size: 1.25rem;
            line-height: 1.25;
            margin-bottom: 0.15rem;
        }

        .bet-card .torneio {
            color: var(--muted);
            font-size: 0.88rem;
            margin-bottom: 0.75rem;
        }

        .bet-card .mercado-label {
            color: var(--muted);
            text-transform: uppercase;
            font-size: 0.68rem;
            letter-spacing: 0.04em;
            font-family: 'Work Sans', sans-serif;
            margin-bottom: 0.15rem;
        }

        .bet-card .mercado-valor {
            font-family: 'Work Sans', sans-serif;
            font-weight: 500;
            font-size: 1rem;
            white-space: normal;
            word-wrap: break-word;
            overflow-wrap: break-word;
            line-height: 1.35;
            margin-bottom: 0.8rem;
        }

        .bet-card .info-row {
            display: flex;
            flex-wrap: wrap;
            gap: 1.5rem;
            margin-bottom: 0.8rem;
        }

        .bet-card .info-item .info-label {
            color: var(--muted);
            text-transform: uppercase;
            font-size: 0.68rem;
            letter-spacing: 0.04em;
            margin-bottom: 0.1rem;
        }

        .bet-card .info-item .info-valor {
            font-family: 'JetBrains Mono', monospace;
            font-weight: 700;
            font-size: 1.05rem;
        }

        .bet-card .placar-final {
            background: rgba(34,197,94,0.14);
            color: var(--green);
            border-radius: 10px;
            padding: 0.5rem 0.8rem;
            font-family: 'Work Sans', sans-serif;
            font-weight: 600;
            font-size: 0.92rem;
            margin-bottom: 0.8rem;
        }
        .bet-card .placar-final .placar-numeros {
            font-family: 'JetBrains Mono', monospace;
            font-weight: 700;
        }

        .bet-card .badges-row {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            flex-wrap: wrap;
            gap: 0.35rem;
            flex-shrink: 0;
        }

        .status-badge, .resultado-badge {
            display: inline-block;
            padding: 0.2rem 0.65rem;
            border-radius: 999px;
            font-family: 'Work Sans', sans-serif;
            font-weight: 600;
            font-size: 0.75rem;
            white-space: nowrap;
        }

        .status-agendada { background: rgba(34,197,94,0.16); color: var(--green); }
        .status-ao-vivo { background: rgba(239,68,68,0.16); color: var(--red); }
        .status-alerta { background: rgba(215,242,77,0.14); color: var(--lime); }
        .status-encerrada { background: rgba(154,160,172,0.16); color: var(--muted); }

        .resultado-green { background: rgba(34,197,94,0.16); color: var(--green); }
        .resultado-red { background: rgba(239,68,68,0.16); color: var(--red); }
        .resultado-void { background: rgba(154,160,172,0.16); color: var(--muted); }
        .resultado-pendente { background: rgba(215,242,77,0.14); color: var(--lime); }

        /* Sugestão automática de conferência (aposta bateu/não bateu) */
        .sugestao-conferencia {
            border-radius: 10px;
            padding: 0.45rem 0.75rem;
            font-family: 'Work Sans', sans-serif;
            font-weight: 600;
            font-size: 0.85rem;
            margin-bottom: 0.8rem;
        }

        /* ---------------------------------------------------------------
           Editor de status/resultado — visualmente "colado" na base do
           card acima (mesmo fundo, sem borda entre os dois, cantos
           inferiores arredondados só aqui) em vez de dois selects nativos
           soltos por baixo do card.
        --------------------------------------------------------------- */
        div[class*="st-key-editor_"] {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-top: 1px solid rgba(255,255,255,0.06);
            border-radius: 0 0 16px 16px;
            padding: 0.5rem 0.9rem 0.7rem;
            margin-bottom: 0.9rem;
        }
        /* Força as duas colunas lado a lado mesmo no mobile — o Streamlit
           dá min-width:calc(100% - 24px) pras colunas em telas estreitas
           (pensado pra empilhar), então isso também precisa ser anulado,
           não só o flex-direction do bloco pai. */
        div[class*="st-key-editor_"] [data-testid="stHorizontalBlock"] {
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.6rem;
        }
        div[class*="st-key-editor_"] [data-testid="stColumn"] {
            min-width: 0 !important;
            width: 50% !important;
            flex: 1 1 0 !important;
        }
        div[class*="st-key-editor_"] [data-baseweb="select"] > div {
            min-height: 2.1rem !important;
            background: rgba(255,255,255,0.04) !important;
            border-color: var(--card-border) !important;
            border-radius: 8px !important;
            font-size: 0.78rem !important;
        }

        /* ---------------------------------------------------------------
           Botões das casas de apostas — uma linha só, com "logo"
           (monograma colorido) de cada casa.
        --------------------------------------------------------------- */
        .bookmaker-row {
            display: flex;
            gap: 0.6rem;
        }

        .bookmaker-btn {
            flex: 1 1 0;
            display: flex;
            align-items: center;
            justify-content: center;
            border: none;
            border-radius: 10px;
            padding: 0.6rem 0.5rem;
            text-decoration: none !important;
            filter: brightness(1);
            transition: filter 0.15s ease;
            min-width: 0;
            height: 2.7rem;
            box-sizing: border-box;
        }
        .bookmaker-btn:hover {
            filter: brightness(1.12);
        }

        .bookmaker-logo-img {
            height: 1.35rem;
            width: auto;
            max-width: 85%;
            object-fit: contain;
        }

        .bookmaker-fallback {
            color: #fff;
            font-family: 'Archivo', sans-serif;
            font-weight: 800;
            font-size: 0.75rem;
            letter-spacing: 0.02em;
        }

        .bookmaker-vazio {
            color: var(--muted);
            font-size: 0.85rem;
        }

        /* ---------------------------------------------------------------
           Sidebar — a mesma tipografia/hierarquia do resto do painel.
        --------------------------------------------------------------- */
        section[data-testid="stSidebar"] {
            background: var(--card-bg);
            border-right: 1px solid var(--card-border);
        }
        section[data-testid="stSidebar"] h1 {
            font-size: 1.3rem !important;
            margin-bottom: 0.2rem;
        }
        section[data-testid="stSidebar"] .stMultiSelect label,
        section[data-testid="stSidebar"] .stDateInput label {
            font-family: 'Work Sans', sans-serif;
            color: var(--muted);
            text-transform: uppercase;
            font-size: 0.7rem;
            letter-spacing: 0.04em;
            font-weight: 600;
        }
        section[data-testid="stSidebar"] .stButton button {
            background: var(--lime);
            color: #14161a;
            font-weight: 700;
            border: none;
            border-radius: 10px;
        }

        /* Menos espaço entre widgets empilhados no card (selects de status/resultado) */
        div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stSelectbox"]) {
            margin-bottom: 0.4rem;
        }

        /* Métricas do topo mais compactas — menos respiro vertical entre
           os blocos nativos do Streamlit. */
        div[data-testid="stMetric"] {
            padding: 0.35rem 0 !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"],
        div[data-testid="element-container"] {
            margin-bottom: 0 !important;
        }
        hr {
            margin: 0.8rem 0 !important;
        }

        /* ---------------------------------------------------------------
           Grid de métricas do topo — HTML/CSS puro (não st.columns/
           st.metric) pra controlar quantas colunas cabem também no mobile
           (ver _metrics_grid() e o media query abaixo). Minimalista de
           propósito (sem cards/fundo/borda — pedido do usuário): a
           hierarquia vem só de tamanho/peso de fonte.

           Dois grupos com pesos visuais diferentes:
             - .metrics-grid-destaque (Green/Red/Unidades — o resultado
               financeiro, o que mais importa) — 3 colunas, fonte maior.
             - .metrics-grid comum (Total/Ao vivo/Odd média/Void/Pendente/
               Taxa de acerto — contexto geral) — mais colunas, mais
               discreto, uma linha fina acima separa visualmente dos itens
               em destaque sem precisar de caixinha.
        --------------------------------------------------------------- */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.7rem 0.5rem;
            margin-bottom: 0.9rem;
            padding-top: 0.7rem;
            border-top: 1px solid var(--card-border);
        }
        .metrics-grid-destaque {
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.5rem;
            margin-bottom: 0;
            padding-top: 0;
            border-top: none;
        }
        .metric-cell { min-width: 0; }
        .metric-label {
            font-family: 'Work Sans', sans-serif;
            color: var(--muted);
            text-transform: uppercase;
            font-size: 0.66rem;
            letter-spacing: 0.04em;
            margin-bottom: 0.15rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .metric-valor {
            font-family: 'JetBrains Mono', monospace;
            font-weight: 700;
            font-size: 1.05rem;
            line-height: 1.15;
        }
        .metrics-grid-destaque .metric-label { font-size: 0.72rem; }
        .metrics-grid-destaque .metric-valor { font-size: 1.9rem; }
        .metric-delta {
            font-family: 'Work Sans', sans-serif;
            font-size: 0.72rem;
            color: var(--muted);
            margin-top: 0.1rem;
        }

        /* ---------------------------------------------------------------
           Responsividade mobile: reduz padding/fonte e força qualquer
           bloco horizontal nativo do Streamlit (ex: grupos de botões) a
           empilhar em vez de espremer.
        --------------------------------------------------------------- */
        @media (max-width: 640px) {
            .app-header { font-size: 1.6rem; }

            .bet-card { padding: 0.85rem 0.9rem; }
            .bet-card .jogo { font-size: 1.08rem; }
            .bet-card .info-row { gap: 1rem; }

            .metrics-grid {
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 0.6rem 0.4rem;
            }
            .metric-valor { font-size: 0.95rem; }
            .metrics-grid-destaque .metric-valor { font-size: 1.5rem; }

            div[data-testid="stHorizontalBlock"]:not([class*="editor_"] *) {
                flex-direction: column !important;
            }
            div[data-testid="stHorizontalBlock"]:not([class*="editor_"] *) > div {
                width: 100% !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _db_host_label() -> str:
    """Só o host do Postgres (nunca a connection string inteira, que carrega
    senha) — exibido na sidebar como referência de qual banco está ativo."""
    url = settings.DATABASE_URL
    if not url:
        return "não configurado"
    # postgresql://user:pass@host/db?... -> host
    sem_esquema = url.split("://", 1)[-1]
    sem_credenciais = sem_esquema.split("@", 1)[-1]
    host = sem_credenciais.split("/", 1)[0]
    return host


def _sidebar_filters() -> tuple[list[str], date, date]:
    st.sidebar.title("🎾 Filtros")

    status_opcoes = list(STATUS_LABELS.keys())
    status_labels_sel = st.sidebar.multiselect(
        "Status",
        options=[STATUS_LABELS[s] for s in status_opcoes],
        default=[STATUS_LABELS[BetStatus.AGENDADA.value], STATUS_LABELS[BetStatus.AO_VIVO.value]],
    )
    label_to_status = {v: k for k, v in STATUS_LABELS.items()}
    status_selecionados = [label_to_status[l] for l in status_labels_sel] or status_opcoes

    hoje = date.today()
    intervalo = st.sidebar.date_input(
        "Período (data do jogo)",
        value=(hoje, hoje.replace(day=min(28, hoje.day)) + pd.Timedelta(days=14)),
    )
    if isinstance(intervalo, tuple) and len(intervalo) == 2:
        data_de, data_ate = intervalo
    else:
        data_de = data_ate = intervalo

    if st.sidebar.button("🔄 Atualizar agora"):
        st.rerun()

    st.sidebar.caption(f"Banco: `{_db_host_label()}`")
    return status_selecionados, data_de, data_ate


def _calcular_estatisticas(apostas: list[Bet]) -> dict:
    """
    Estatísticas sobre o conjunto filtrado, baseadas no campo `resultado`
    (green/red/void/pendente) — definido manualmente pelo usuário, não pelo
    pipeline. Void e pendente não entram em taxa de acerto/ROI (não houve
    ganho/perda real a contabilizar).
    """
    green = [b for b in apostas if b.resultado == ResultadoAposta.GREEN.value]
    red = [b for b in apostas if b.resultado == ResultadoAposta.RED.value]
    void = [b for b in apostas if b.resultado == ResultadoAposta.VOID.value]
    pendente = [b for b in apostas if b.resultado == ResultadoAposta.PENDENTE.value]

    decididas = len(green) + len(red)
    taxa_acerto = (len(green) / decididas * 100) if decididas else None

    lucro_green = sum(b.unidades * (b.odd - 1) for b in green if b.odd is not None)
    perda_red = sum(b.unidades for b in red)
    unidades_liquidas = lucro_green - perda_red

    unidades_apostadas = sum(b.unidades for b in green) + sum(b.unidades for b in red)
    roi = (unidades_liquidas / unidades_apostadas * 100) if unidades_apostadas else None

    return {
        "green": len(green),
        "red": len(red),
        "void": len(void),
        "pendente": len(pendente),
        "taxa_acerto": taxa_acerto,
        "unidades_liquidas": unidades_liquidas,
        "roi": roi,
    }


def _bet_card(bet: Bet) -> None:
    _log_debug_info(bet)

    status_badge_class = STATUS_CSS_CLASS.get(bet.status, "status-alerta")
    status_label = STATUS_LABELS.get(bet.status, bet.status)
    resultado_badge_class = RESULTADO_CSS_CLASS.get(bet.resultado, "resultado-pendente")
    resultado_label = RESULTADO_LABELS.get(bet.resultado, bet.resultado)

    mercado_txt = bet.mercado or "Mercado não identificado"
    odd_txt = f"{bet.odd:.2f}" if bet.odd is not None else "—"
    data_txt = bet.data_hora.strftime("%d/%m %H:%M") if bet.data_hora else "não encontrada"

    placar_html = ""
    if bet.placar_final:
        placar_html = (
            f'<div class="placar-final">🏆 {bet.vencedor_partida or "?"} venceu '
            f'<span class="placar-numeros">{bet.placar_final}</span></div>'
        )

    # Sugestão automática de conferência — só exibida quando a partida já
    # terminou e o mercado é do tipo "{Nome} ganhar"; o usuário ainda decide
    # via selectbox, isto é só uma dica visual pra agilizar.
    sugestao_html = ""
    sugestao = _sugerir_resultado(bet) if bet.resultado == ResultadoAposta.PENDENTE.value else None
    if sugestao:
        sugestao_class = "resultado-green" if sugestao == ResultadoAposta.GREEN.value else "resultado-red"
        sugestao_label = "bateu (green)" if sugestao == ResultadoAposta.GREEN.value else "não bateu (red)"
        sugestao_html = (
            f'<div class="sugestao-conferencia {sugestao_class}">'
            f'🔍 Conferência automática: sua aposta {sugestao_label}</div>'
        )

    # Logos reais das casas com link exato/aproximado — uma linha só, botão
    # inteiro na cor de marca.
    links_html = ""
    if bet.links:
        botoes = []
        for slug, info in bet.links.items():
            badge = BOOKMAKER_BADGES.get(slug)
            if badge:
                logo_html = f'<img class="bookmaker-logo-img" src="{badge["logo"]}" alt="{info.get("nome", "?")}" />'
                cor = badge["cor"]
            else:
                logo_html = f'<span class="bookmaker-fallback">{(info.get("nome") or "?")[:3].upper()}</span>'
                cor = "#6b7280"
            botoes.append(
                f'<a class="bookmaker-btn" style="background:{cor}" href="{info["url"]}" target="_blank" rel="noopener">'
                f'{logo_html}'
                f'</a>'
            )
        links_html = f'<div class="bookmaker-row">{"".join(botoes)}</div>'
    else:
        links_html = '<div class="bookmaker-vazio">Nenhuma casa de apostas configurada/encontrada.</div>'

    card_html = (
        f'<div class="bet-card">'
        f'<div class="card-header-row">'
        f'<div class="jogo">{bet.jogo}</div>'
        f'<div class="badges-row">'
        f'<span class="status-badge {status_badge_class}">{status_label}</span>'
        f'<span class="resultado-badge {resultado_badge_class}">{resultado_label}</span>'
        f'</div>'
        f'</div>'
        f'<div class="torneio">{bet.torneio or "Torneio não identificado"}</div>'
        f'<div class="mercado-label">Mercado</div>'
        f'<div class="mercado-valor">{mercado_txt}</div>'
        f'<div class="info-row">'
        f'<div class="info-item"><div class="info-label">Odd</div><div class="info-valor">{odd_txt}</div></div>'
        f'<div class="info-item"><div class="info-label">Unidades</div><div class="info-valor">{bet.unidades:.1f}</div></div>'
        f'<div class="info-item"><div class="info-label">Data/Hora</div><div class="info-valor">{data_txt}</div></div>'
        f'</div>'
        f'{placar_html}'
        f'{sugestao_html}'
        f'{links_html}'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

    with st.container(key=f"editor_{bet.id}"):
        col_status, col_resultado = st.columns(2)
        with col_status:
            novo_status = st.selectbox(
                "Atualizar status",
                options=list(STATUS_LABELS.keys()),
                format_func=lambda s: STATUS_LABELS[s],
                index=list(STATUS_LABELS.keys()).index(bet.status) if bet.status in STATUS_LABELS else 0,
                key=f"status_{bet.id}",
                label_visibility="collapsed",
            )
        with col_resultado:
            novo_resultado = st.selectbox(
                "Resultado da aposta",
                options=list(RESULTADO_LABELS.keys()),
                format_func=lambda r: RESULTADO_LABELS[r],
                index=list(RESULTADO_LABELS.keys()).index(bet.resultado) if bet.resultado in RESULTADO_LABELS else 0,
                key=f"resultado_{bet.id}",
                label_visibility="collapsed",
            )

    if novo_status != bet.status:
        update_status(bet.id, novo_status)
        st.rerun()
    if novo_resultado != bet.resultado:
        update_resultado(bet.id, novo_resultado)
        st.rerun()


def _metrics_grid(itens: list[tuple[str, str, str | None]], destaque: bool = False) -> None:
    """
    Grid de métricas em HTML/CSS puro (não st.columns/st.metric) — no
    desktop fica em linha(s) de várias colunas, mas no mobile também usa um
    grid compacto (3 colunas) em vez de empilhar 1 métrica por linha, que é
    o comportamento padrão de st.columns em telas estreitas e deixava o
    topo do painel comprido demais (muito scroll até o primeiro jogo).
    Cada item é (label, valor, delta_opcional).

    `destaque=True` (usado só pro grupo Green/Red/Unidades, o resultado
    financeiro — ver main()) aumenta o peso visual (fonte maior) SEM usar
    cards com fundo/borda — só hierarquia tipográfica, visual minimalista
    pedido pelo usuário em vez de caixinhas.
    """
    classe_grid = "metrics-grid metrics-grid-destaque" if destaque else "metrics-grid"
    celulas = []
    for label, valor, delta in itens:
        delta_html = f'<div class="metric-delta">{delta}</div>' if delta else ""
        celulas.append(
            f'<div class="metric-cell">'
            f'<div class="metric-label">{label}</div>'
            f'<div class="metric-valor">{valor}</div>'
            f"{delta_html}"
            f"</div>"
        )
    st.markdown(f'<div class="{classe_grid}">{"".join(celulas)}</div>', unsafe_allow_html=True)


def main() -> None:
    _inject_css()

    st.markdown('<div class="app-header">Cansadão Apostas</div>', unsafe_allow_html=True)

    status_sel, data_de, data_ate = _sidebar_filters()

    apostas: list[Bet] = []
    for status in status_sel:
        apostas.extend(
            list_bets(
                status=status,
                date_from=str(data_de) if data_de else None,
                date_to=str(data_ate) if data_ate else None,
            )
        )
    # remove duplicatas (caso um bet apareça em mais de um filtro) e ordena por data
    apostas_unicas = {b.id: b for b in apostas}.values()
    apostas_ordenadas = sorted(
        apostas_unicas, key=lambda b: b.data_hora or datetime.max
    )

    stats = _calcular_estatisticas(apostas_ordenadas)
    odd_media = (
        f"{(sum(b.odd for b in apostas_ordenadas if b.odd) / max(1, sum(1 for b in apostas_ordenadas if b.odd))):.2f}"
        if any(b.odd for b in apostas_ordenadas) else "—"
    )
    # Resultado financeiro (o que mais importa: ganhando ou perdendo) em
    # destaque tipográfico; resumo geral (contexto) abaixo, mais discreto —
    # separação visual pedida pelo usuário, sem usar cards com fundo/borda.
    _metrics_grid(
        [
            ("✅ Green", str(stats["green"]), None),
            ("❌ Red", str(stats["red"]), None),
            (
                "Unidades (líquido)",
                f"{stats['unidades_liquidas']:+.2f}",
                f"ROI {stats['roi']:.1f}%" if stats["roi"] is not None else None,
            ),
        ],
        destaque=True,
    )
    _metrics_grid(
        [
            ("Total no filtro", str(len(apostas_ordenadas)), None),
            ("Ao vivo agora", str(sum(1 for b in apostas_ordenadas if b.status == BetStatus.AO_VIVO.value)), None),
            ("Odd média", odd_media, None),
            ("➖ Void", str(stats["void"]), None),
            ("⏳ Pendente", str(stats["pendente"]), None),
            ("Taxa de acerto", f"{stats['taxa_acerto']:.1f}%" if stats["taxa_acerto"] is not None else "—", None),
        ]
    )

    st.divider()

    if not apostas_ordenadas:
        st.info("Nenhuma aposta encontrada com os filtros atuais. Ajuste os filtros na barra lateral.")
        return

    for bet in apostas_ordenadas:
        _bet_card(bet)


if __name__ == "__main__":
    main()
