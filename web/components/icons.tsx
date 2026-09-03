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

export function HourglassIcon({ color = "currentColor", size = 14 }: IconProps) {
  return (
    <svg {...base(size)} stroke={color}>
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

export function DotIcon({ color = "currentColor", size = 10 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 10 10">
      <circle cx="5" cy="5" r="5" fill={color} />
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
