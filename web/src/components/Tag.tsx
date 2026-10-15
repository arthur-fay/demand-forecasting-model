/**
 * Evidence-status tags: the visual signature of ATLAS. Every number on screen carries
 * one, saying what it is (observed, projected, hypothetical, diagnostic) and how far it
 * can be trusted (FAILED_CALIBRATION, not significant). Each tag has an icon and a text
 * label, so status is never carried by colour alone.
 */
import type { Integrity } from '../api/schemas';
import { AlertOctagon, AlertTriangle, Archive, Check, Eye, Flask, Info, Ruler } from './icons';

export type TagTone =
  'neutral' | 'observed' | 'projected' | 'warning' | 'serious' | 'critical' | 'good';

const ICONS = {
  neutral: Info,
  observed: Eye,
  projected: Archive,
  warning: Flask,
  serious: AlertTriangle,
  critical: AlertOctagon,
  good: Check,
} as const;

export function Tag({
  tone = 'neutral',
  children,
  title,
}: {
  tone?: TagTone;
  children: React.ReactNode;
  title?: string;
}) {
  const IconComponent = ICONS[tone];
  return (
    <span className="tag" data-tone={tone} title={title}>
      {tone === 'observed' || tone === 'projected' ? (
        <span className="tag-key" aria-hidden="true" />
      ) : (
        <IconComponent />
      )}
      {children}
    </span>
  );
}

/** The M3 interval status, printed in upper case as the API asks (FAILED_CALIBRATION). */
export function IntervalStatusTag({
  status,
}: {
  status: Integrity['uncertainty_interval_status'];
}) {
  const tone: TagTone = status === 'decision_grade' ? 'good' : 'critical';
  return (
    <Tag tone={tone} title="M3 interval status, from the committed calibration_status.json">
      {status.toUpperCase()}
    </Tag>
  );
}

export function HypotheticalTag() {
  return (
    <Tag tone="warning" title="Every operational parameter is assumed, not measured">
      HYPOTHETICAL
    </Tag>
  );
}

export function DiagnosticTag() {
  return (
    <Tag
      tone="serious"
      title="No plan meets every HYPOTHETICAL constraint: values of the relaxed plan"
    >
      DIAGNOSTIC
    </Tag>
  );
}

export function NotValidatedTag() {
  return (
    <Tag tone="warning" title="point_forecast_validated: false">
      Not validated
    </Tag>
  );
}

const DATA_STATUS: Record<
  Integrity['data_status'],
  { label: string; tone: TagTone; title: string }
> = {
  observed: { label: 'Observed', tone: 'observed', title: 'Recorded history (legacy-v0 snapshot)' },
  projected: {
    label: 'Archived projection',
    tone: 'projected',
    title: 'Forecasts made from 30 Jun 2026 for days not in the data: not live, not validated',
  },
  historical_evaluation: {
    label: 'Historical backtest',
    tone: 'neutral',
    title: 'Scores of re-forecasts of past days',
  },
  calibration_assessment: {
    label: 'Calibration assessment',
    tone: 'neutral',
    title: 'The M3 calibration verdict',
  },
  hypothetical_reference_run: {
    label: 'Reference run',
    tone: 'neutral',
    title: 'The committed M4 reference run',
  },
  hypothetical_simulation: {
    label: 'Simulation',
    tone: 'neutral',
    title: 'One interactive M4 simulation',
  },
  metadata: { label: 'Metadata', tone: 'neutral', title: 'Service metadata' },
};

/** Every tag the integrity block calls for, in a fixed order. */
export function IntegrityTags({ integrity }: { integrity: Integrity }) {
  const status = DATA_STATUS[integrity.data_status];
  return (
    <span className="tag-row">
      <Tag tone={status.tone} title={status.title}>
        {status.label}
      </Tag>
      {integrity.parameter_provenance === 'HYPOTHETICAL' && <HypotheticalTag />}
      {integrity.data_status === 'projected' && <NotValidatedTag />}
    </span>
  );
}

export { Ruler };
