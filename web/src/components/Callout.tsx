import { AlertOctagon, AlertTriangle, Flask, Info } from './icons';

export type CalloutTone = 'info' | 'warning' | 'serious' | 'critical';

const ICON = {
  info: Info,
  warning: Flask,
  serious: AlertTriangle,
  critical: AlertOctagon,
} as const;

/** A warning or note placed next to the numbers it qualifies. */
export function Callout({
  tone = 'info',
  title,
  children,
  role,
}: {
  tone?: CalloutTone;
  title: React.ReactNode;
  children?: React.ReactNode;
  /** `alert` only for failures the user just caused (a rejected simulation). */
  role?: 'alert' | 'status' | 'note';
}) {
  const IconComponent = ICON[tone];
  return (
    <div className="callout" data-tone={tone} role={role ?? 'note'}>
      <IconComponent />
      <div className="callout-body">
        <p className="callout-title">{title}</p>
        {children}
      </div>
    </div>
  );
}
