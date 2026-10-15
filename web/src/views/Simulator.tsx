import { useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, describeError } from '../api/client';
import { api } from '../api/endpoints';
import type {
  Envelope,
  ParameterSpec,
  SimulationData,
  SimulationParametersData,
  SimulationRequest,
} from '../api/schemas';
import { Callout } from '../components/Callout';
import { DataTable } from '../components/DataTable';
import { Evidence } from '../components/Provenance';
import { Async } from '../components/States';
import { HypotheticalTag, IntegrityTags, Tag } from '../components/Tag';
import { useDeployment } from '../deployment';
import {
  formatDate,
  formatInteger,
  formatNumber,
  formatUnit,
  humanise,
  shortHash,
} from '../lib/format';
import { Link } from '../router';
import { PageHeader } from './common';
import { useSimulationParameters } from './data';
import { FeasibilityTag, PlanOutcome } from './plan';
import {
  changedFields,
  fieldOfDetail,
  groupParameters,
  initialForm,
  toRequest,
  validate,
  type FieldErrors,
  type FormState,
} from './simulatorForm';

interface Answer {
  readonly result: Envelope<SimulationData>;
  readonly request: SimulationRequest;
  readonly ms: number;
}

type Run =
  | { readonly status: 'idle' }
  | { readonly status: 'running'; readonly startedAt: number; readonly controller: AbortController }
  | { readonly status: 'success' }
  | { readonly status: 'error'; readonly error: unknown };

function fieldId(key: string): string {
  return `sim-${key.replace(/[^a-z0-9_]/gi, '-')}`;
}

function NumberField({
  id,
  label,
  unit,
  value,
  onChange,
  error,
  help,
  min,
  max,
  step = 'any',
  disabled,
  slider = false,
  changed = false,
}: {
  id: string;
  label: string;
  unit: string;
  value: string;
  onChange: (text: string) => void;
  error?: string | undefined;
  help?: React.ReactNode;
  min: number;
  max: number;
  step?: number | 'any';
  disabled: boolean;
  slider?: boolean;
  changed?: boolean;
}) {
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = [error !== undefined ? errorId : null, help !== undefined ? helpId : null]
    .filter(Boolean)
    .join(' ');
  return (
    <div className="field" data-changed={changed ? 'true' : undefined}>
      <label className="field-label" htmlFor={id}>
        <span>
          {label}
          {changed && (
            <span className="changed-dot" aria-label="(changed)">
              {' '}
              •
            </span>
          )}
        </span>
        <span className="unit">{unit}</span>
      </label>
      <div className={slider ? 'field-row' : undefined}>
        {slider && (
          <input
            type="range"
            min={min}
            max={max}
            step={step}
            value={Number.isFinite(Number(value)) ? Number(value) : min}
            onChange={(event) => {
              onChange(event.target.value);
            }}
            disabled={disabled}
            aria-label={`${label} slider`}
            aria-valuetext={`${value} ${unit}`}
            tabIndex={-1}
          />
        )}
        <input
          id={id}
          className="input"
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
          }}
          disabled={disabled}
          aria-invalid={error !== undefined}
          aria-describedby={describedBy || undefined}
        />
      </div>
      {error !== undefined && (
        <span id={errorId} className="field-error">
          {error}
        </span>
      )}
      {help !== undefined && (
        <span id={helpId} className="field-help">
          {help}
        </span>
      )}
    </div>
  );
}

function ParameterField({
  spec,
  form,
  setForm,
  errors,
  disabled,
}: {
  spec: ParameterSpec;
  form: FormState;
  setForm: (update: (form: FormState) => FormState) => void;
  errors: FieldErrors;
  disabled: boolean;
}) {
  const field = form.parameters[spec.name] ?? { text: '', unlimited: false };
  const key = `parameters.${spec.name}`;
  const changed =
    field.unlimited !== (spec.default === null) ||
    (!field.unlimited && field.text !== String(spec.default ?? ''));
  const update = (next: Partial<typeof field>) => {
    setForm((current) => ({
      ...current,
      parameters: { ...current.parameters, [spec.name]: { ...field, ...next } },
    }));
  };
  return (
    <div className="stack-2">
      <NumberField
        id={fieldId(key)}
        label={humanise(spec.name)}
        unit={formatUnit(spec.unit)}
        value={field.unlimited ? '' : field.text}
        onChange={(text) => {
          update({ text });
        }}
        error={errors[key]}
        min={spec.minimum}
        max={spec.maximum}
        disabled={disabled || field.unlimited}
        changed={changed}
        help={`Range ${formatNumber(spec.minimum, spec.minimum % 1 === 0 ? 0 : 1)}–${formatInteger(spec.maximum)}. Reference: ${spec.default === null ? 'no limit' : formatNumber(spec.default, spec.default % 1 === 0 ? 0 : 2)}.`}
      />
      {spec.nullable && (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={field.unlimited}
            disabled={disabled}
            onChange={(event) => {
              update({
                unlimited: event.target.checked,
                text: field.text === '' ? String(spec.default ?? spec.maximum) : field.text,
              });
            }}
          />
          No limit
        </label>
      )}
      <details className="field-help">
        <summary>Reference assumption</summary>
        {spec.default_assumption}
      </details>
    </div>
  );
}

function SimulatorForm({
  spec,
  blocked,
}: {
  spec: SimulationParametersData;
  blocked: string | null;
}) {
  const [form, setFormState] = useState<FormState>(() => initialForm(spec));
  const [touched, setTouched] = useState(false);
  const [serverErrors, setServerErrors] = useState<FieldErrors>({});
  const [run, setRun] = useState<Run>({ status: 'idle' });
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const summary = useRef<HTMLDivElement>(null);
  const clientErrors = useMemo(() => validate(form, spec), [form, spec]);
  const errors: FieldErrors = { ...(touched ? clientErrors : {}), ...serverErrors };
  const changed = changedFields(form, spec);
  const disabled = blocked !== null;
  const running = run.status === 'running';

  const setForm = (update: (current: FormState) => FormState) => {
    setFormState(update);
    setServerErrors({});
  };

  useEffect(() => {
    if (run.status !== 'running') return;
    const timer = setInterval(() => {
      setElapsed(Date.now() - run.startedAt);
    }, 200);
    return () => {
      clearInterval(timer);
    };
  }, [run]);

  useEffect(
    () => () => {
      // Leaving the page cancels a simulation still waiting for its answer.
      if (run.status === 'running') run.controller.abort();
    },
    [run],
  );

  const submit = async (event: React.SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (disabled || running) return;
    setTouched(true);
    setServerErrors({});
    if (Object.keys(clientErrors).length > 0) {
      summary.current?.focus();
      return;
    }
    const request = toRequest(form, spec);
    const controller = new AbortController();
    const startedAt = Date.now();
    setElapsed(0);
    setRun({ status: 'running', startedAt, controller });
    try {
      const result = await api.simulate(request, { signal: controller.signal });
      setAnswer({ result, request, ms: Date.now() - startedAt });
      setRun({ status: 'success' });
    } catch (error) {
      if (error instanceof ApiError && error.kind === 'aborted') {
        setRun({ status: 'idle' });
        return;
      }
      if (error instanceof ApiError && error.details.length > 0) {
        const mapped: Record<string, string> = {};
        for (const detail of error.details) mapped[fieldOfDetail(detail.field)] = detail.message;
        setServerErrors(mapped);
      }
      setRun({ status: 'error', error });
      requestAnimationFrame(() => summary.current?.focus());
    }
  };

  const errorEntries = Object.entries(errors);
  return (
    <div className="stack-4">
      <form
        className="panel simulator-form"
        onSubmit={(event) => void submit(event)}
        noValidate
        aria-labelledby="sim-form-title"
      >
        <div className="panel-header">
          <div className="panel-title">
            <h2 id="sim-form-title">Hypothetical inputs</h2>
            <p className="subtitle">
              Start from the reference configuration and change what you want to explore. Only
              changed values are sent; the rest keep their reference value.
            </p>
            <HypotheticalTag />
          </div>
        </div>
        {blocked !== null && (
          <Callout tone="info" title="Interactive simulations are disabled in this deployment">
            <p>{blocked}</p>
            <p>
              The controls show what could be adjusted and the reference values. The nine committed
              reference scenarios are on the <Link href="/decisions">Decision optimisation</Link>{' '}
              page.
            </p>
          </Callout>
        )}
        <div ref={summary} tabIndex={-1} className="stack-2">
          {errorEntries.length > 0 && (
            <Callout
              tone="critical"
              role="alert"
              title={`${errorEntries.length} ${errorEntries.length === 1 ? 'input needs' : 'inputs need'} attention`}
            >
              <ul>
                {errorEntries.map(([key, message]) => (
                  <li key={key}>
                    {key === '<body>' ? (
                      message
                    ) : (
                      <a href={`#${fieldId(key)}`}>
                        {humanise(key.replace(/^(parameters|demand)\./, ''))}: {message}
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </Callout>
          )}
        </div>
        <fieldset className="sim-group" disabled={disabled}>
          <legend>Horizon and demand</legend>
          <div className="sim-grid">
            <NumberField
              id={fieldId('horizon_days')}
              label="Planning horizon"
              unit="days"
              value={form.horizon}
              onChange={(text) => {
                setForm((f) => ({ ...f, horizon: text }));
              }}
              error={errors.horizon_days}
              min={spec.horizon_days.minimum}
              max={spec.horizon_days.maximum}
              step={1}
              slider
              disabled={disabled}
              changed={form.horizon !== String(spec.horizon_days.default)}
              help={`From ${formatDate(spec.horizon_days.first_date)}, the first projected day. Horizon totals (overtime, surge, budget) are not prorated.`}
            />
            <div className="field">
              <span className="field-label" id="basis-label">
                Demand path
              </span>
              <div className="segmented" role="radiogroup" aria-labelledby="basis-label">
                {spec.demand.basis.map((basis) => (
                  <label key={basis}>
                    <input
                      type="radio"
                      name="basis"
                      value={basis}
                      checked={form.basis === basis}
                      disabled={disabled}
                      onChange={() => {
                        setForm((f) => ({ ...f, basis }));
                      }}
                    />
                    {basis === 'point_forecast' ? 'Point forecast' : 'M3 quantile stress path'}
                  </label>
                ))}
              </div>
              {form.basis === 'm3_marginal_quantile' && (
                <>
                  <label className="field-label" htmlFor={fieldId('demand.quantile')}>
                    Quantile level
                  </label>
                  <select
                    id={fieldId('demand.quantile')}
                    className="select"
                    value={form.quantile}
                    disabled={disabled}
                    onChange={(event) => {
                      setForm((f) => ({ ...f, quantile: event.target.value }));
                    }}
                  >
                    {spec.demand.quantile.map((q) => (
                      <option key={q} value={String(q)}>
                        {q}
                      </option>
                    ))}
                  </select>
                  <span className="field-help">{spec.demand.notice}</span>
                </>
              )}
            </div>
            <NumberField
              id={fieldId('demand.multiplier')}
              label="Demand multiplier"
              unit="×"
              value={form.multiplier}
              onChange={(text) => {
                setForm((f) => ({ ...f, multiplier: text }));
              }}
              error={errors['demand.multiplier']}
              min={spec.demand.multiplier.minimum}
              max={spec.demand.multiplier.maximum}
              step={0.01}
              slider
              disabled={disabled}
              changed={form.multiplier !== String(spec.demand.multiplier.default)}
              help="Every day's demand multiplied by this factor."
            />
            <NumberField
              id={fieldId('demand.shift_per_day')}
              label="Demand shift"
              unit="requests/day"
              value={form.shift}
              onChange={(text) => {
                setForm((f) => ({ ...f, shift: text }));
              }}
              error={errors['demand.shift_per_day']}
              min={spec.demand.shift_per_day.minimum}
              max={spec.demand.shift_per_day.maximum}
              step={1}
              slider
              disabled={disabled}
              changed={form.shift !== String(spec.demand.shift_per_day.default)}
              help="Added to every day's demand (for example the forecast's historical under-forecast)."
            />
          </div>
        </fieldset>
        {groupParameters(spec.parameters).map((group) => (
          <fieldset key={group.title} className="sim-group" disabled={disabled}>
            <legend>{group.title}</legend>
            <div className="sim-grid">
              {group.specs.map((p) => (
                <ParameterField
                  key={p.name}
                  spec={p}
                  form={form}
                  setForm={setForm}
                  errors={errors}
                  disabled={disabled}
                />
              ))}
            </div>
          </fieldset>
        ))}
        <p className="xsmall muted">
          {spec.notes.join(' ')} Legacy demand percentile:{' '}
          {formatNumber(spec.legacy_demand_percentile_requests_per_day, 2)} requests/day.
        </p>
        <div className="sim-actions">
          <button
            type="submit"
            className="button"
            data-variant="primary"
            disabled={disabled || running}
          >
            {running ? 'Running…' : 'Run simulation'}
          </button>
          <button
            type="button"
            className="button"
            disabled={disabled || changed.length === 0 || running}
            onClick={() => {
              setFormState(initialForm(spec));
              setServerErrors({});
              setTouched(false);
            }}
          >
            Reset to reference
          </button>
          {running && (
            <button
              type="button"
              className="button"
              onClick={() => {
                run.controller.abort();
              }}
            >
              Cancel
            </button>
          )}
          <span className="small secondary" aria-live="polite">
            {running
              ? `Solving on the server… ${(elapsed / 1000).toFixed(1)} s`
              : changed.length === 0
                ? 'No change: this runs the reference configuration.'
                : `${changed.length} ${changed.length === 1 ? 'change' : 'changes'} from the reference: ${changed.map(humanise).join(', ')}.`}
          </span>
        </div>
      </form>
      {run.status === 'error' && <SimulationError error={run.error} />}
      {answer !== null && (
        <div className="stack-2" data-stale={run.status === 'success' ? undefined : 'true'}>
          {run.status !== 'success' && (
            <p className="small secondary">
              Showing the previous result: it does not reflect the inputs of the last attempt.
            </p>
          )}
          <SimulationResult result={answer.result} request={answer.request} ms={answer.ms} />
        </div>
      )}
    </div>
  );
}

function SimulationError({ error }: { error: unknown }) {
  const { title, detail } = describeError(error);
  const api = error instanceof ApiError ? error : null;
  let explanation: string | null = null;
  if (api?.code === 'simulation_timeout' || api?.kind === 'timeout')
    explanation =
      'The server stopped the solve at its deadline and replaced the worker. Nothing was computed; try a shorter horizon or simpler inputs.';
  else if (api?.code === 'simulation_rejected')
    explanation =
      'M4 refused these inputs as stated (its own rules, such as never using a demand percentile as a capacity). The fields named above explain why.';
  else if (api?.code === 'validation_error')
    explanation = 'The API rejected an input; the fields are marked above.';
  else if (api?.code === 'simulations_busy')
    explanation = 'Every simulation worker is busy and the API keeps no queue. Retry in a moment.';
  else if (api?.code === 'simulation_failed')
    explanation =
      'The solver could not certify a result, so the API returned no plan rather than an unverified one.';
  else if (api?.status === 401)
    explanation =
      'This deployment requires a key for simulations, which the dashboard never holds.';
  else if (api?.code === 'rate_limited')
    explanation = 'The proxy limits how many simulations one client can run per minute.';
  return (
    <div className="state" data-kind="error" role="alert">
      <p className="state-title">Simulation not run: {title}</p>
      <p>{detail}</p>
      {explanation !== null && <p>{explanation}</p>}
      {api?.requestId != null && <p className="request-id">Request id {api.requestId}</p>}
    </div>
  );
}

function SimulationResult({
  result,
  request,
  ms,
}: {
  result: Envelope<SimulationData>;
  request: SimulationRequest;
  ms: number;
}) {
  const { data, integrity, provenance } = result;
  const headingRef = useRef<HTMLHeadingElement>(null);
  // Each new result takes the focus, so keyboard and screen-reader users land on it.
  useEffect(() => {
    headingRef.current?.focus();
  }, [result]);
  return (
    <section className="panel" aria-labelledby="sim-result-title">
      <div className="panel-header">
        <div className="panel-title">
          <h2 id="sim-result-title" tabIndex={-1} ref={headingRef}>
            Simulation result <FeasibilityTag feasibility={data.outcome.feasibility} />
          </h2>
          <p className="subtitle">
            {data.horizon_days} days, {formatDate(data.first_date)} to {formatDate(data.last_date)}{' '}
            · simulation{' '}
            <span className="mono" title={data.simulation_id}>
              {shortHash(data.simulation_id, 12)}
            </span>{' '}
            · answered in {(ms / 1000).toFixed(2)} s
          </p>
          <IntegrityTags integrity={integrity} />
        </div>
      </div>
      <Callout tone="warning" title="An exploratory mathematical demonstration">
        <p>{data.label}</p>
      </Callout>
      {data.demand_notice !== null && (
        <Callout tone="warning" title="Demand stress path">
          <p>{data.demand_notice}</p>
        </Callout>
      )}
      <PlanOutcome outcome={data.outcome} plans={data.plans} daily={data.daily_plans} />
      <Callout tone="info" title="Limitations of this simulation (from the API)">
        <ul>
          {data.limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      </Callout>
      <details className="disclosure">
        <summary>Every input of this run ({data.parameters.length})</summary>
        <div className="disclosure-body">
          <DataTable
            caption="Resolved inputs: what you sent (request), reference defaults, and removed limits. All HYPOTHETICAL."
            rows={data.parameters}
            rowKey={(p) => p.name}
            highlight={(p) => p.source !== 'reference_default'}
            columns={[
              { key: 'name', header: 'Input', rowHeader: true, cell: (p) => humanise(p.name) },
              {
                key: 'value',
                header: 'Value',
                numeric: true,
                cell: (p) =>
                  p.value === null
                    ? 'no limit'
                    : `${formatNumber(p.value, p.value % 1 === 0 ? 0 : 2)} ${formatUnit(p.unit)}`,
              },
              {
                key: 'source',
                header: 'Source',
                cell: (p) =>
                  p.source === 'request' ? (
                    <Tag tone="warning">Your value</Tag>
                  ) : p.source === 'removed_by_request' ? (
                    'Removed'
                  ) : (
                    'Reference'
                  ),
              },
              {
                key: 'assumption',
                header: 'Assumption',
                cell: (p) => p.reference_assumption ?? p.assumption,
              },
            ]}
          />
          <p className="xsmall muted">
            Request sent: <code>{JSON.stringify(request)}</code>
          </p>
        </div>
      </details>
      <details className="disclosure">
        <summary>Structural assumptions ({data.structural_assumptions.length})</summary>
        <div className="disclosure-body">
          <ul className="notice-list">
            {data.structural_assumptions.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </div>
      </details>
      <Evidence integrity={integrity} provenance={provenance} />
    </section>
  );
}

export default function Simulator() {
  const parameters = useSimulationParameters();
  const { simulationsBlocked } = useDeployment();
  return (
    <>
      <PageHeader eyebrow="M5 · Bounded HYPOTHETICAL simulations" title="Hypothetical simulator">
        <p>
          Change the assumed capacity, overtime, surge, budget and backlog values and re-run the M4
          linear programme on the server. Each run is one bounded, certified solve of the real
          engine: the results are exactly what the model computes for the values you chose, and
          nothing more.
        </p>
      </PageHeader>
      <Callout tone="warning" title="Simulations are exploratory, not operational advice">
        <p>
          Every input is HYPOTHETICAL and the demand is an unvalidated point forecast. A simulation
          shows how the model responds to assumptions; it is not a validated staffing, budget or
          service-capacity recommendation.
        </p>
      </Callout>
      <Async resource={parameters} label="the simulation inputs">
        {({ data }) => <SimulatorForm spec={data} blocked={simulationsBlocked} />}
      </Async>
    </>
  );
}
