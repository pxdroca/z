"""

app.py
======
Dashboard Streamlit — painel web visual para as apostas capturadas pelo
listener. Lê diretamente do mesmo arquivo SQLite (data/apostas.db) que o
listener.py escreve, então basta rodar os dois processos em paralelo:

    # terminal 1
    python listener.py

    # terminal 2
    streamlit run app.py

O Streamlit é gratuito e roda 100% localmente; para "compartilhar com
outros membros" (como pedido), você pode:
  - deixar rodando na sua máquina e compartilhar na rede local
    (`streamlit run app.py --server.address 0.0.0.0`), ou
  - publicar de graça no Streamlit Community Cloud (streamlit.io/cloud).

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

from config import settings
from database import auto_promote_ao_vivo, init_db, list_bets, update_resultado, update_status
from models import Bet, BetStatus, ResultadoAposta
from nameutils import names_match

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

# "Logo" de cada casa — sem baixar imagens externas (mais confiável/rápido):
# monograma de 2-3 letras com a cor de marca de cada uma.
BOOKMAKER_BADGES = {
    "superbet": {"sigla": "SB", "cor": "#e2001a"},
    "betano": {"sigla": "BET", "cor": "#03c04a"},
    "bet365": {"sigla": "365", "cor": "#136c2e"},
}


def _sugerir_resultado(bet: Bet) -> str | None:
    """
    Quando a partida termina e o mercado é do tipo "{Nome} ganhar" (o único
    padrão que o extractor.py monta automaticamente hoje — ver
    extractor.py::_infer_market_from_favorite), compara o vencedor real da
    partida com o nome citado no mercado e sugere green/red.

    Só uma SUGESTÃO exibida no card — quem confirma é sempre o usuário
    (novo_resultado no selectbox), nunca grava sozinho. Mercados que não
    seguem esse padrão (handicap, games, sets) não têm como ser inferidos
    automaticamente, então não sugerem nada (None).
    """
    if not bet.placar_final or not bet.vencedor_partida or not bet.mercado:
        return None
    if not bet.mercado.lower().endswith("ganhar"):
        return None
    jogador_citado = bet.mercado.rsplit(" ", 1)[0].strip()
    if names_match(jogador_citado, bet.vencedor_partida, threshold=80):
        return ResultadoAposta.GREEN.value
    return ResultadoAposta.RED.value


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
           App header
        --------------------------------------------------------------- */
        .app-header {
            font-family: 'Archivo', sans-serif;
            font-weight: 800;
            font-size: 2.1rem;
            letter-spacing: -0.02em;
            margin-bottom: 0.1rem;
        }
        .app-subheader {
            color: var(--muted);
            font-family: 'Work Sans', sans-serif;
            margin-bottom: 1.2rem;
        }

        /* ---------------------------------------------------------------
           Card da aposta — HTML puro (não st.metric/st.columns) para
           controlar quebra de linha do mercado e ser 100% responsivo.
        --------------------------------------------------------------- */
        .bet-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.1rem 1.3rem;
            margin-bottom: 0.9rem;
            box-shadow: 0 2px 10px rgba(0,0,0,0.25);
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
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-bottom: 0.9rem;
        }

        .status-badge, .resultado-badge {
            display: inline-block;
            padding: 0.2rem 0.65rem;
            border-radius: 999px;
            font-family: 'Work Sans', sans-serif;
            font-weight: 600;
            font-size: 0.8rem;
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
            gap: 0.4rem;
            background: rgba(255,255,255,0.04);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            padding: 0.55rem 0.5rem;
            text-decoration: none !important;
            transition: background 0.15s ease;
            min-width: 0;
        }
        .bookmaker-btn:hover {
            background: rgba(255,255,255,0.09);
        }

        .bookmaker-logo {
            flex-shrink: 0;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 1.6rem;
            height: 1.6rem;
            border-radius: 6px;
            color: #fff;
            font-family: 'Archivo', sans-serif;
            font-weight: 800;
            font-size: 0.6rem;
            letter-spacing: -0.02em;
        }

        .bookmaker-nome {
            color: var(--textColor, #e8e9ec);
            font-family: 'Work Sans', sans-serif;
            font-weight: 600;
            font-size: 0.85rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
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
           Responsividade mobile: reduz padding/fonte e força qualquer
           bloco horizontal nativo do Streamlit (ex: grupos de botões) a
           empilhar em vez de espremer.
        --------------------------------------------------------------- */
        @media (max-width: 640px) {
            .app-header { font-size: 1.6rem; }

            .bet-card { padding: 0.85rem 0.9rem; }
            .bet-card .jogo { font-size: 1.08rem; }
            .bet-card .info-row { gap: 1rem; }

            .bookmaker-row {
                flex-direction: column;
            }
            .bookmaker-nome {
                overflow: visible;
                white-space: normal;
            }

            div.row-widget.stHorizontal {
                flex-direction: column !important;
            }
            div.row-widget.stHorizontal > div {
                width: 100% !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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

    st.sidebar.caption(f"Banco: `{settings.DB_PATH}`")
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

    # Logos (monograma) das casas com link exato/aproximado — uma linha só.
    links_html = ""
    if bet.links:
        botoes = []
        for slug, info in bet.links.items():
            badge = BOOKMAKER_BADGES.get(slug, {"sigla": (info.get("nome") or "?")[:3].upper(), "cor": "#6b7280"})
            icone = "🎯" if info.get("exato") else "📍"
            botoes.append(
                f'<a class="bookmaker-btn" href="{info["url"]}" target="_blank" rel="noopener">'
                f'<span class="bookmaker-logo" style="background:{badge["cor"]}">{badge["sigla"]}</span>'
                f'<span class="bookmaker-nome">{icone} {info.get("nome", "?")}</span>'
                f'</a>'
            )
        links_html = f'<div class="bookmaker-row">{"".join(botoes)}</div>'
    else:
        links_html = '<div class="bookmaker-vazio">Nenhuma casa de apostas configurada/encontrada.</div>'

    card_html = (
        f'<div class="bet-card">'
        f'<div class="jogo">{bet.jogo}</div>'
        f'<div class="torneio">{bet.torneio or "Torneio não identificado"}</div>'
        f'<div class="mercado-label">Mercado</div>'
        f'<div class="mercado-valor">{mercado_txt}</div>'
        f'<div class="info-row">'
        f'<div class="info-item"><div class="info-label">Odd</div><div class="info-valor">{odd_txt}</div></div>'
        f'<div class="info-item"><div class="info-label">Unidades</div><div class="info-valor">{bet.unidades:.1f}</div></div>'
        f'<div class="info-item"><div class="info-label">Data/Hora</div><div class="info-valor">{data_txt}</div></div>'
        f'</div>'
        f'{placar_html}'
        f'<div class="badges-row">'
        f'<span class="status-badge {status_badge_class}">{status_label}</span>'
        f'<span class="resultado-badge {resultado_badge_class}">{resultado_label}</span>'
        f'</div>'
        f'{sugestao_html}'
        f'{links_html}'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

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


def main() -> None:
    _inject_css()

    st.markdown('<div class="app-header">🎾 Cansadão Apostas</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="app-subheader">Apostas capturadas automaticamente do grupo de tips, cruzadas com a Superbet.</div>',
        unsafe_allow_html=True,
    )

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

    linha1 = st.columns(3)
    linha1[0].metric("Total no filtro", len(apostas_ordenadas))
    linha1[1].metric("Ao vivo agora", sum(1 for b in apostas_ordenadas if b.status == BetStatus.AO_VIVO.value))
    linha1[2].metric(
        "Odd média",
        f"{(sum(b.odd for b in apostas_ordenadas if b.odd) / max(1, sum(1 for b in apostas_ordenadas if b.odd))):.2f}"
        if any(b.odd for b in apostas_ordenadas) else "—",
    )

    stats = _calcular_estatisticas(apostas_ordenadas)
    linha2 = st.columns(6)
    linha2[0].metric("✅ Green", stats["green"])
    linha2[1].metric("❌ Red", stats["red"])
    linha2[2].metric("➖ Void", stats["void"])
    linha2[3].metric("⏳ Pendente", stats["pendente"])
    linha2[4].metric(
        "Taxa de acerto",
        f"{stats['taxa_acerto']:.1f}%" if stats["taxa_acerto"] is not None else "—",
    )
    linha2[5].metric(
        "Unidades (líquido)",
        f"{stats['unidades_liquidas']:+.2f}",
        delta=f"ROI {stats['roi']:.1f}%" if stats["roi"] is not None else None,
    )

    st.divider()

    if not apostas_ordenadas:
        st.info("Nenhuma aposta encontrada com os filtros atuais. Ajuste os filtros na barra lateral.")
        return

    for bet in apostas_ordenadas:
        _bet_card(bet)


if __name__ == "__main__":
    main()
