/** Inline SVG icons (no icon font, no external file): decorative, hidden from assistive tech. */
import type { SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export const AlertTriangle = (p: IconProps) => (
  <Icon {...p}>
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    <path d="M12 9v4M12 17h.01" />
  </Icon>
);

export const AlertOctagon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9Z" />
    <path d="M12 8v4M12 16h.01" />
  </Icon>
);

export const Info = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 16v-4M12 8h.01" />
  </Icon>
);

export const Check = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20 6 9 17l-5-5" />
  </Icon>
);

export const Flask = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 3h6M10 3v6.5L4.6 18.4A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.4-2.6L14 9.5V3" />
    <path d="M7.5 15h9" />
  </Icon>
);

export const Archive = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3" y="4" width="18" height="5" rx="1" />
    <path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" />
  </Icon>
);

export const Ruler = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 17 17 3l4 4L7 21Z" />
    <path d="m7 13 2 2M10 10l2 2M13 7l2 2" />
  </Icon>
);

export const Lock = (p: IconProps) => (
  <Icon {...p}>
    <rect x="4" y="11" width="16" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </Icon>
);

export const Menu = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Icon>
);

export const Close = (p: IconProps) => (
  <Icon {...p}>
    <path d="M18 6 6 18M6 6l12 12" />
  </Icon>
);

export const Eye = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
    <circle cx="12" cy="12" r="3" />
  </Icon>
);

/** The ATLAS mark: a globe's meridians and parallels, drawn as a measuring grid. */
export function AtlasMark(props: IconProps) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false" {...props}>
      <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="2" />
      <ellipse
        cx="16"
        cy="16"
        rx="5.5"
        ry="13"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path d="M3.5 12h25M3.5 20h25" stroke="currentColor" strokeWidth="1.5" />
      <path d="M16 3v26" style={{ stroke: 'var(--series-2)' }} strokeWidth="2" />
    </svg>
  );
}
