// Ícones de contorno (linha fina, estilo Notion/Linear) em vez de emoji —
// pedido do usuário ("troque os emojis por ícones, os de green e red com
// cor, pra ficar mais bonito"). Cada um recebe a cor via prop `color`
// (normalmente uma das CSS custom properties de globals.css).

type IconProps = { color?: string; size?: number };

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
});

export function CheckIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

export function XIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

export function MinusIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M5 12h14" />
    </svg>
  );
}

export function PlusIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M5 12h14" />
      <path d="M12 5v14" />
    </svg>
  );
}

export function HourglassIcon({ color = "currentColor", size = 14, spin = false }: IconProps & { spin?: boolean }) {
  return (
    <svg {...base(size)} stroke={color} className={spin ? "icon-spin" : undefined}>
      <path d="M5 22h14" />
      <path d="M5 2h14" />
      <path d="M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22" />
      <path d="M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2" />
    </svg>
  );
}

export function CoinIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <circle cx="12" cy="12" r="9" />
      <path d="M14.5 9.5a2.5 2.5 0 0 0-2.5-1.5c-1.5 0-2.5 1-2.5 2s1 1.5 2.5 2 2.5 1 2.5 2-1 2-2.5 2a2.5 2.5 0 0 1-2.5-1.5" />
      <path d="M12 6.5v11" />
    </svg>
  );
}

export function DotIcon({ color = "currentColor", size = 10, pulse = false }: IconProps & { pulse?: boolean }) {
  if (!pulse) {
    return (
      <svg width={size} height={size} viewBox="0 0 10 10">
        <circle cx="5" cy="5" r="5" fill={color} />
      </svg>
    );
  }
  // "Live" real: um halo transparente pulsa (escala + desvanece) atrás do
  // ponto sólido, que fica parado — efeito clássico de indicador ao vivo,
  // não a bolinha inteira mudando de tamanho.
  return (
    <svg width={size * 2} height={size * 2} viewBox="0 0 20 20" style={{ overflow: "visible" }}>
      <circle cx="10" cy="10" r="5" fill={color} className="dot-pulse-halo" />
      <circle cx="10" cy="10" r="5" fill={color} />
    </svg>
  );
}

export function AlertIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M12 9v4" />
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L14.71 3.86a2 2 0 0 0-3.42 0Z" />
      <path d="M12 17h.01" />
    </svg>
  );
}

export function SquareIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color} fill={color}>
      <rect x="4" y="4" width="16" height="16" rx="2" />
    </svg>
  );
}

export function PencilIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
      <path d="m15 5 4 4" />
    </svg>
  );
}

export function SearchIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}

export function TrophyIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M8 21h8" />
      <path d="M12 17v4" />
      <path d="M7 4h10v5a5 5 0 0 1-10 0Z" />
      <path d="M17 5h3a2 2 0 0 1-2 4h-1" />
      <path d="M7 5H4a2 2 0 0 0 2 4h1" />
    </svg>
  );
}

export function RefreshIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

export function FilterIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M3 4.5h18l-7 8.5v6l-4 2v-8L3 4.5Z" />
    </svg>
  );
}

export function ChevronDownIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function TrendUpIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M22 7 13.5 15.5l-5-5L2 17" />
      <path d="M16 7h6v6" />
    </svg>
  );
}

export function TrendDownIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M22 17 13.5 8.5l-5 5L2 7" />
      <path d="M16 17h6v-6" />
    </svg>
  );
}

export function LayersIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="m12 2 9 5-9 5-9-5 9-5Z" />
      <path d="m3 12 9 5 9-5" />
      <path d="m3 17 9 5 9-5" />
    </svg>
  );
}

export function TargetIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.4" />
    </svg>
  );
}

export function MoreIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <circle cx="12" cy="5" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="12" cy="19" r="1" />
    </svg>
  );
}

/** Bola de tênis estilizada — mesmo estilo de contorno fino dos demais ícones. */
export function TennisBallIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <circle cx="12" cy="12" r="9" />
      <path d="M4.2 7.8C7.5 9 9.3 12.2 8.2 16.3" />
      <path d="M19.8 16.2c-3.3-1.2-5.1-4.4-4-8.5" />
    </svg>
  );
}

/** Bola de basquete estilizada — mesmo estilo de contorno fino dos demais ícones. */
export function BasketballIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3v18" />
      <path d="M5.6 5.6c2.4 1.9 3.8 4 3.8 6.4s-1.4 4.5-3.8 6.4" />
      <path d="M18.4 5.6c-2.4 1.9-3.8 4-3.8 6.4s1.4 4.5 3.8 6.4" />
    </svg>
  );
}

/** Cadeado — usado no campo de senha da tela de login. */
export function LockIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <rect x="4" y="10.5" width="16" height="10" rx="2" />
      <path d="M8 10.5V7a4 4 0 0 1 8 0v3.5" />
    </svg>
  );
}

/** Camera/imagem — usado no botão de exportar resumo do dia. */
export function ImageIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="9" cy="9" r="2" />
      <path d="m21 15-5-5L5 21" />
    </svg>
  );
}

/** Seta pra baixo dentro de uma bandeja — usado como ícone de "baixar imagem". */
export function DownloadIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="M7 10l5 5 5-5" />
      <path d="M12 15V3" />
    </svg>
  );
}

/** Duas folhas sobrepostas — "copiar". */
export function CopyIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

/** Caixa com seta saindo pra cima — "compartilhar" (share sheet do iOS). */
export function ShareIcon({ color = "currentColor", size = 15 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
      <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5" />
      <path d="M16 7l-4-4-4 4" />
      <path d="M12 3v13" />
    </svg>
  );
}
