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
from datetime import date, datetime, timezone

import pandas as pd
import streamlit as st

import resultado_checker
from database import auto_promote_ao_vivo, init_db, list_bets, update_resultado, update_status
from models import Bet, BetStatus, ResultadoAposta

logger = logging.getLogger("app")

st.set_page_config(page_title="Cansadão Apostas", page_icon="🎯", layout="wide")

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

# Fundo do botão = cor de marca sólida; logo real (recolorida para branco)
# por cima. bet365 é exceção: o SVG oficial usa duas cores (o "bet" em
# branco, o "365" em amarelo) — recolorir esse traçado inteiro pra branco
# sólido (como as outras duas casas) apaga a cor característica da marca,
# então usa um wordmark de texto (mesma paleta oficial: fundo verde-escuro
# #027b5b, "bet" branco + "365" amarelo) em vez do SVG traçado.
BOOKMAKER_BADGES = {
    "superbet": {"logo": LOGO_SUPERBET, "cor": "#e2001a"},
    "betano": {"logo": LOGO_BETANO, "cor": "#ff5000"},
    "bet365": {"wordmark": True, "cor": "#027b5b"},
}


def _autoconferir_resultado(bet: Bet) -> str | None:
    """
    Grava automaticamente green/red pra apostas que por algum motivo ainda
    estão "pendente" apesar do jogo já ter terminado — normalmente
    score_updater.py já grava isso sozinho assim que a partida encerra (ver
    resultado_checker.checar_resultado), mas uma partida "encerrada" nunca
    mais é revisitada por ele (list_trackable_bets só cobre agendada/ao_vivo),
    então se por qualquer motivo aquele ciclo não conseguiu decidir (ex:
    EventStatus.sets ainda incompleto no instante exato em que o SofaScore
    marcou a partida como finished — condição de corrida observada em
    produção), fica pendente pra sempre sem isto aqui.

    Mesma lógica central de resultado_checker.py (vencedor de partida,
    vencedor de set específico/genérico, vencer sem perder set), mas
    reconstruída a partir do placar_final/vencedor_partida já persistidos
    (ver checar_resultado_de_bet) em vez do EventStatus ao vivo. Grava
    direto no banco (update_resultado) — decisão do usuário: a conferência
    desses mercados deve ser automática, não uma sugestão que espera
    confirmação manual. Mercados que resultado_checker.py não sabe
    interpretar (aces, games, dupla falta, placar exato) continuam
    pendentes, aguardando conferência manual do usuário.
    """
    resultado = resultado_checker.checar_resultado_de_bet(bet)
    if resultado:
        update_resultado(bet.id, resultado)
        bet.resultado = resultado
    return resultado


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
           inferior direito) e o avatar/perfil ao lado dela — só aparecem
           pro dono do app, nunca pros visitantes, mas não têm função pro
           uso deste painel.

           No HTML real de produção (streamlit.app) o Streamlit Cloud usa
           classes com hash de build (ex: "_viewerBadge_aycw8_23",
           "_profileContainer_gzau3_53" — o hash muda a cada versão), e o
           elemento raiz da badge é um <a>, não um <div> — por isso
           "div[class*=viewerBadge]" nunca batia (restringia a tag errada).
           Seletor sem restrição de tag + [class*=...] cobre o hash mudando
           entre builds.
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
        [class*="viewerBadge"],
        [class*="profileContainer"] {
            display: none !important;
        }
        /* Sem sidebar nativa (filtros viraram popover no header — ver
           _header_e_filtros), então não sobra mais o espaço vazio que a
           barra da sidebar deixava acima do conteúdo: só o respiro mínimo
           do topo da página. */
        div[data-testid="stAppViewContainer"] > div:first-child {
            padding-top: 0.5rem;
        }
        div[data-testid="stMainBlockContainer"] {
            padding-top: 1.5rem !important;
        }

        /* ---------------------------------------------------------------
           App header
        --------------------------------------------------------------- */
        .app-header {
            font-family: 'Archivo', sans-serif;
            font-weight: 800;
            font-size: 2.1rem;
            letter-spacing: -0.02em;
            margin-bottom: 0;
            line-height: 1.4rem;
            padding-top: 0.3rem;
        }

        /* Cabeçalho de cada seção de status (Ao vivo/Agendada/Encerrada/...)
           que separa os cards — ver main(). */
        .secao-status {
            font-family: 'Archivo', sans-serif;
            font-weight: 700;
            font-size: 1.05rem;
            margin: 1.4rem 0 0.7rem;
        }
        .secao-status-count {
            font-family: 'JetBrains Mono', monospace;
            font-weight: 500;
            color: var(--muted);
            font-size: 0.9rem;
        }

        /* ---------------------------------------------------------------
           Card da aposta — HTML puro (não st.metric/st.columns) para
           controlar quebra de linha do mercado e ser 100% responsivo.
        --------------------------------------------------------------- */
        .bet-card {
            position: relative;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.1rem 1.3rem;
            box-shadow: 0 2px 10px rgba(0,0,0,0.25);
        }
        /* Quando o editor está aberto embaixo (ver .st-key-editor_), o
           card perde o arredondado/borda inferior pra "colar" nele. */
        .bet-card.com-editor-aberto {
            border-bottom: none;
            border-radius: 16px 16px 0 0;
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

        /* Placar parcial ao vivo — canto inferior direito do CARD (pedido
           do usuário: separado dos badges de status, que ficam no topo),
           sem o "venceu" (a partida ainda não acabou). Cor neutra pra não
           competir visualmente com o badge vermelho "Ao vivo" no topo.
           padding-bottom extra no card (ver .bet-card.com-placar-ao-vivo)
           abre espaço abaixo dos botões de casa de apostas pra não ficar
           sobreposto/cortado por eles. */
        .bet-card.com-placar-ao-vivo {
            padding-bottom: 2.6rem;
        }
        .bet-card .placar-ao-vivo {
            position: absolute;
            right: 1.3rem;
            bottom: 0.85rem;
            font-family: 'JetBrains Mono', monospace;
            font-weight: 700;
            font-size: 0.85rem;
            color: var(--muted);
        }

        .bet-card .badges-row {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            flex-wrap: wrap;
            gap: 0.35rem;
            flex-shrink: 0;
            /* Espaço reservado pro botão de lápis sobreposto (ver
               st-key-card_ abaixo) não colidir com os badges. */
            padding-right: 1.8rem;
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

        /* ---------------------------------------------------------------
           Wrapper do card inteiro (HTML do card + botão de lápis + editor
           quando aberto) — position:relative pra poder sobrepor o botão
           de lápis por cima do canto superior direito do card via CSS
           absoluto, já que ele não pode ir "dentro" do bloco de HTML puro
           (card_html é st.markdown; um st.button real não entra nele).
        --------------------------------------------------------------- */
        div[class*="st-key-card_"] {
            position: relative;
            margin-bottom: 0.9rem;
        }
        /* O pai imediato do stButton (stElementContainer) também é
           position:relative por padrão do próprio Streamlit — isso faz o
           position:absolute do botão ficar preso dentro dele (que só tem
           a altura do botão), em vez de posicionar relativo ao card
           inteiro. "static" nele deixa o absolute do botão "atravessar"
           até o ancestral maior de verdade (o card, via st-key-card_). */
        div[class*="st-key-card_"] > div[data-testid="stElementContainer"]:has([data-testid="stButton"]) {
            position: static;
        }
        div[class*="st-key-card_"] [data-testid="stButton"] {
            position: absolute;
            top: 1.1rem;
            right: 1.3rem;
            width: auto !important;
            left: auto !important;
        }
        /* Ícone de lápis/X — SVG de contorno (linha fina, estilo
           Notion/Linear) via background-image, não emoji. O texto do
           botão (label=" ", vazio de verdade) fica escondido com
           font-size:0 pra não deixar nenhum espaço/caractere visível. */
        div[class*="st-key-card_"] [data-testid="stButton"] button {
            background-color: transparent !important;
            background-repeat: no-repeat !important;
            background-position: center !important;
            background-size: 15px 15px !important;
            border: none !important;
            font-size: 0 !important;
            width: 1.7rem !important;
            height: 1.7rem !important;
            min-height: 0 !important;
            min-width: 0 !important;
            padding: 0 !important;
            opacity: 0.75;
        }
        div[class*="st-key-card_"] [data-testid="stButton"] button:hover {
            opacity: 1;
        }
        div[class*="st-key-toggle_editor_open_"] button {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%239aa0ac' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z'/%3E%3Cpath d='m15 5 4 4'/%3E%3C/svg%3E") !important;
        }
        div[class*="st-key-toggle_editor_open_"] button:hover {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23d7f24d' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z'/%3E%3Cpath d='m15 5 4 4'/%3E%3C/svg%3E") !important;
        }
        div[class*="st-key-toggle_editor_close_"] button {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%239aa0ac' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M18 6 6 18'/%3E%3Cpath d='m6 6 12 12'/%3E%3C/svg%3E") !important;
        }
        div[class*="st-key-toggle_editor_close_"] button:hover {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23d7f24d' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M18 6 6 18'/%3E%3Cpath d='m6 6 12 12'/%3E%3C/svg%3E") !important;
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
            margin-top: -0.9rem;
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

        /* bet365 usa texto em vez do SVG traçado (ver BOOKMAKER_BADGES) —
           paleta oficial: "bet" em branco, "365" em amarelo. */
        .bookmaker-wordmark-bet365 {
            color: #fff;
            font-family: 'Archivo', sans-serif;
            font-weight: 800;
            font-size: 1.05rem;
            letter-spacing: -0.01em;
        }
        .bookmaker-wordmark-bet365 .accent {
            color: #f9dc1c;
        }

        .bookmaker-vazio {
            color: var(--muted);
            font-size: 0.85rem;
        }

        /* ---------------------------------------------------------------
           Popover de filtros (canto superior direito do header) — mesma
           tipografia/hierarquia do resto do painel.
        --------------------------------------------------------------- */
        div[data-testid="stPopoverBody"] .stMultiSelect label,
        div[data-testid="stPopoverBody"] .stDateInput label {
            font-family: 'Work Sans', sans-serif;
            color: var(--muted);
            text-transform: uppercase;
            font-size: 0.7rem;
            letter-spacing: 0.04em;
            font-weight: 600;
        }
        div[data-testid="stPopoverBody"] .stButton button {
            background: var(--lime);
            color: #14161a;
            font-weight: 700;
            border: none;
            border-radius: 10px;
        }
        /* O botão que abre o popover ("🔍 Filtros") alinhado à direita, na
           mesma linha do título — ver _header_e_filtros. */
        div[data-testid="stPopover"] > button {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            color: #fff;
            font-weight: 600;
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
            display: flex;
            flex-wrap: wrap;
            gap: 0.7rem 2rem;
            margin-bottom: 0.9rem;
            padding-top: 0.7rem;
            border-top: 1px solid var(--card-border);
        }
        .metrics-grid-destaque {
            gap: 2.2rem;
            margin-bottom: 0;
            padding-top: 0;
            border-top: none;
        }
        .metric-cell { min-width: 0; flex: 0 0 auto; }
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
            /* No mobile a sidebar já nasce colapsada e a barra nativa do
               Streamlit some — o padding-top pensado pro desktop (pra não
               ficar embaixo da seta "‹"/"›") sobra como espaço vazio aqui,
               então reduz bem mais nesse breakpoint. */
            .main .block-container { padding-top: 1rem !important; }

            .app-header { font-size: 1.6rem; }

            .bet-card { padding: 0.85rem 0.9rem; }
            .bet-card .jogo { font-size: 1.08rem; }
            .bet-card .info-row { gap: 1rem; }

            .metrics-grid {
                display: grid;
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


def _header_e_filtros() -> tuple[list[str], date, date]:
    """
    Título + filtros na mesma linha do topo, sem sidebar — pedido do
    usuário pra eliminar o espaço vazio que a sidebar nativa do Streamlit
    deixava acima dela (existia mesmo movida pra direita via CSS, ver
    histórico). Os filtros ficam atrás de um st.popover compacto no canto
    superior direito em vez de ocupar largura fixa da tela o tempo todo.
    """
    col_titulo, col_filtro = st.columns([5, 1])
    with col_titulo:
        st.markdown('<div class="app-header">Cansadão Apostas</div>', unsafe_allow_html=True)

    with col_filtro:
        with st.popover("🔍 Filtros", use_container_width=True):
            status_opcoes = list(STATUS_LABELS.keys())
            status_labels_sel = st.multiselect(
                "Status",
                options=[STATUS_LABELS[s] for s in status_opcoes],
                default=[
                    STATUS_LABELS[BetStatus.AGENDADA.value],
                    STATUS_LABELS[BetStatus.AO_VIVO.value],
                    STATUS_LABELS[BetStatus.ENCERRADA.value],
                ],
            )
            label_to_status = {v: k for k, v in STATUS_LABELS.items()}
            status_selecionados = [label_to_status[l] for l in status_labels_sel] or status_opcoes

            hoje = date.today()
            intervalo = st.date_input(
                "Período (data do jogo)",
                value=(hoje, hoje.replace(day=min(28, hoje.day)) + pd.Timedelta(days=14)),
            )
            if isinstance(intervalo, tuple) and len(intervalo) == 2:
                data_de, data_ate = intervalo
            else:
                data_de = data_ate = intervalo

            if st.button("🔄 Atualizar agora", use_container_width=True):
                st.rerun()

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

    # Se o jogo já encerrou mas o resultado ainda ficou "pendente" (o
    # score_updater.py não conseguiu decidir no ciclo em que a partida
    # terminou, e depois de "encerrada" ele nunca mais reprocessa essa
    # aposta), tenta conferir e gravar agora — ver _autoconferir_resultado.
    if bet.status == BetStatus.ENCERRADA.value and bet.resultado == ResultadoAposta.PENDENTE.value:
        _autoconferir_resultado(bet)

    status_badge_class = STATUS_CSS_CLASS.get(bet.status, "status-alerta")
    status_label = STATUS_LABELS.get(bet.status, bet.status)
    resultado_badge_class = RESULTADO_CSS_CLASS.get(bet.resultado, "resultado-pendente")
    resultado_label = RESULTADO_LABELS.get(bet.resultado, bet.resultado)

    mercado_txt = bet.mercado or "Mercado não identificado"
    odd_txt = f"{bet.odd:.2f}" if bet.odd is not None else "—"
    data_txt = bet.data_hora.strftime("%d/%m %H:%M") if bet.data_hora else "não encontrada"

    # placar_final guarda "o placar mais recente conhecido" (ver
    # score_updater.py::_processar_bet) — quando a partida já encerrou,
    # mostra o resultado final ("🏆 X venceu"); enquanto ainda está ao
    # vivo, mostra como placar parcial (sem "venceu", já que ninguém
    # venceu ainda).
    placar_html = ""
    if bet.placar_final and bet.status == BetStatus.ENCERRADA.value:
        placar_html = (
            f'<div class="placar-final">🏆 {bet.vencedor_partida or "?"} venceu '
            f'<span class="placar-numeros">{bet.placar_final}</span></div>'
        )

    # Logos reais das casas com link exato/aproximado — uma linha só, botão
    # inteiro na cor de marca.
    links_html = ""
    if bet.links:
        botoes = []
        for slug, info in bet.links.items():
            badge = BOOKMAKER_BADGES.get(slug)
            if badge and badge.get("wordmark"):
                logo_html = '<span class="bookmaker-wordmark-bet365">bet<span class="accent">365</span></span>'
                cor = badge["cor"]
            elif badge:
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

    editando_key = f"editando_{bet.id}"
    editando = st.session_state.get(editando_key, False)

    # Placar parcial ao vivo — canto inferior direito do card (pedido do
    # usuário: separado dos badges de status, que ficam no topo), via
    # position:absolute sobre o .bet-card. Só quando a partida está
    # rolando de verdade (status ao_vivo) e o SofaScore já reportou algum
    # set jogado.
    tem_placar_ao_vivo = bet.status == BetStatus.AO_VIVO.value and bool(bet.placar_final)
    placar_ao_vivo_html = f'<div class="placar-ao-vivo">{bet.placar_final}</div>' if tem_placar_ao_vivo else ""

    card_classe = "bet-card"
    if editando:
        card_classe += " com-editor-aberto"
    if tem_placar_ao_vivo:
        card_classe += " com-placar-ao-vivo"
    card_html = (
        f'<div class="{card_classe}">'
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
        f'{placar_ao_vivo_html}'
        f'{placar_html}'
        f'{links_html}'
        f'</div>'
    )

    # O botão de lápis precisa ficar visualmente ao lado dos badges de
    # status (canto superior direito do card, pedido do usuário) — como o
    # card em si é HTML puro (st.markdown, pra controlar quebra de linha
    # do mercado), um st.button real não pode ir "dentro" desse HTML.
    # Solução: st.container com position:relative envolvendo card + botão,
    # e o botão posicionado absoluto por cima via CSS (seletor
    # st-key-card_<id> — ver _inject_css).
    with st.container(key=f"card_{bet.id}"):
        st.markdown(card_html, unsafe_allow_html=True)

        # Dois botões com keys fixas e distintas (um por estado, só um
        # renderizado por vez) — permite CSS simples e óbvio pra cada
        # ícone (ver _inject_css: .st-key-toggle_editor_open_<id>::before
        # = lápis; .st-key-toggle_editor_close_<id>::before = X), ambos em
        # SVG de linha fina inline, não emoji (pedido do usuário). Label
        # vazio (só espaço — Streamlit exige texto não-vazio) porque o
        # ícone visível é 100% CSS.
        if not editando:
            if st.button(" ", key=f"toggle_editor_open_{bet.id}", help="Editar status/resultado"):
                st.session_state[editando_key] = True
                st.rerun()
        else:
            if st.button(" ", key=f"toggle_editor_close_{bet.id}", help="Fechar edição"):
                st.session_state[editando_key] = False
                st.rerun()

        if editando:
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

    status_sel, data_de, data_ate = _header_e_filtros()

    apostas: list[Bet] = []
    for status in status_sel:
        apostas.extend(
            list_bets(
                status=status,
                date_from=str(data_de) if data_de else None,
                date_to=str(data_ate) if data_ate else None,
            )
        )
    # remove duplicatas (caso um bet apareça em mais de um filtro)
    apostas_unicas = {b.id: b for b in apostas}.values()
    # data_hora vem timezone-aware (UTC) do banco — datetime.max é naive, e
    # comparar os dois quebra com TypeError; precisa do mesmo tipo aqui.
    _sem_data = datetime.max.replace(tzinfo=timezone.utc)
    # Prioridade pedida pelo usuário: ao vivo primeiro (mais urgente/ação
    # imediata), depois agendada, depois encerrada (já resolvida); erro de
    # extração/não encontrada por último (menos relevantes pro dia a dia).
    # Dentro de cada grupo, mais recente/próximo primeiro.
    _PRIORIDADE_STATUS = {
        BetStatus.AO_VIVO.value: 0,
        BetStatus.AGENDADA.value: 1,
        BetStatus.ENCERRADA.value: 2,
        BetStatus.NAO_ENCONTRADA.value: 3,
        BetStatus.ERRO_EXTRACAO.value: 3,
    }
    apostas_ordenadas = sorted(
        apostas_unicas,
        key=lambda b: (_PRIORIDADE_STATUS.get(b.status, 9), b.data_hora or _sem_data),
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
                "Unidades",
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
        st.info("Nenhuma aposta encontrada com os filtros atuais. Ajuste os filtros no botão de Filtros.")
        return

    # Separa em seções por status (em vez de uma lista só misturada quando
    # vários filtros estão ativos ao mesmo tempo) — mesma ordem de
    # prioridade de _PRIORIDADE_STATUS. groupby precisa da lista já
    # ordenada por essa mesma chave, o que apostas_ordenadas já garante.
    grupos: list[tuple[str, list[Bet]]] = []
    for bet in apostas_ordenadas:
        if grupos and grupos[-1][0] == bet.status:
            grupos[-1][1].append(bet)
        else:
            grupos.append((bet.status, [bet]))

    for status, apostas_do_grupo in grupos:
        st.markdown(
            f'<div class="secao-status">{STATUS_LABELS.get(status, status)} '
            f'<span class="secao-status-count">({len(apostas_do_grupo)})</span></div>',
            unsafe_allow_html=True,
        )
        # 2 jogos por linha no desktop pra aproveitar melhor o espaço
        # horizontal (o media query mobile força de volta pra 1 coluna —
        # ver CSS de stHorizontalBlock em _inject_css). st.columns
        # aninhado dentro de cada _bet_card (o editor de status/resultado)
        # funciona normalmente aqui, é só mais 1 nível de aninhamento.
        for i in range(0, len(apostas_do_grupo), 2):
            par = apostas_do_grupo[i:i + 2]
            colunas = st.columns(2)
            for coluna, bet in zip(colunas, par):
                with coluna:
                    _bet_card(bet)


if __name__ == "__main__":
    main()
