// Port de STATUS_LABELS/RESULTADO_LABELS/STATUS_CSS_CLASS/RESULTADO_CSS_CLASS
// em app.py — mas com o texto/emoji do label separado do ícone: o usuário
// pediu pra trocar os emojis (✅❌➖⏳) por ícones de verdade com cor, então
// aqui só ficam os TEXTOS; os ícones (componentes SVG) ficam em icons.tsx e
// são escolhidos pelo componente a partir do valor de status/resultado.
import type { BetStatus, ResultadoAposta } from "./types";

export const STATUS_LABEL: Record<BetStatus, string> = {
  // "Pendente", não "Agendada": decisão explícita do usuário, a mesma que
  // define o agrupamento em agrupamento.ts ("agendada = pendente, prefiro
  // pendente"). O status no banco continua 'agendada' — só o rótulo na
  // tela muda, pra não haver duas palavras pra mesma coisa no painel.
  agendada: "Pendente",
  ao_vivo: "Ao vivo",
  nao_encontrada: "Não encontrada",
  erro_extracao: "Erro na extração",
  encerrada: "Encerrada",
};

export const RESULTADO_LABEL: Record<ResultadoAposta, string> = {
  pendente: "Pendente",
  green: "Green",
  red: "Red",
  void: "Void",
  cashout: "Cashout",
};

export const STATUS_CSS_CLASS: Record<BetStatus, string> = {
  agendada: "status-agendada",
  ao_vivo: "status-ao-vivo",
  nao_encontrada: "status-alerta",
  erro_extracao: "status-alerta",
  encerrada: "status-encerrada",
};

export const RESULTADO_CSS_CLASS: Record<ResultadoAposta, string> = {
  pendente: "resultado-pendente",
  green: "resultado-green",
  red: "resultado-red",
  void: "resultado-void",
  cashout: "resultado-cashout",
};

// Mesma ordem de prioridade de _PRIORIDADE_STATUS em app.py: ao vivo
// primeiro (mais urgente), depois agendada, depois encerrada (já
// resolvida), erro/não encontrada por último.
export const PRIORIDADE_STATUS: Record<BetStatus, number> = {
  ao_vivo: 0,
  agendada: 1,
  encerrada: 2,
  nao_encontrada: 3,
  erro_extracao: 3,
};
