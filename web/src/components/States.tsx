/** Loading, error and empty states shared by every view. */
import { describeError, ApiError } from '../api/client';
import type { Resource } from '../api/resource';

export function Loading({ label, slow }: { label: string; slow?: boolean }) {
  return (
    <div className="state" role="status" aria-live="polite" aria-busy="true">
      <span className="loading-row">
        <span className="spinner" aria-hidden="true" />
        Loading {label}…
      </span>
      {slow === true && (
        <span className="muted">
          Still waiting for the server: the connection seems slow. The request continues.
        </span>
      )}
    </div>
  );
}

/**
 * Retry replaces the error state (and its button) with the loading state: the focus goes
 * to the heading of the section the error was in (else the page's content), not to the
 * page's start. Only this section's: one request can back several.
 */
function retryFocus(button: HTMLElement) {
  const id = button.closest('section')?.getAttribute('aria-labelledby');
  const target = (id ? document.getElementById(id) : null) ?? button.closest('main');
  if (target === null) return;
  if (!target.hasAttribute('tabindex')) target.tabIndex = -1;
  target.focus();
}

export function ErrorState({
  error,
  label,
  onRetry,
}: {
  error: unknown;
  label: string;
  onRetry?: () => void;
}) {
  const { title, detail, retryable } = describeError(error);
  const api = error instanceof ApiError ? error : null;
  return (
    <div className="state" data-kind="error" role="alert">
      <p className="state-title">
        {title} ({label})
      </p>
      <p>{detail}</p>
      {api !== null && api.details.length > 0 && (
        <ul className="notice-list">
          {api.details.map((item) => (
            <li key={`${item.field}:${item.message}`}>
              <code>{item.field}</code>: {item.message}
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">No numbers are shown in place of the missing ones.</p>
      {api?.requestId != null && <p className="request-id">Request id {api.requestId}</p>}
      {retryable && onRetry !== undefined && (
        <button
          type="button"
          className="button"
          onClick={(event) => {
            retryFocus(event.currentTarget);
            onRetry();
          }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="state">{children}</div>;
}

/** Render `resource` once loaded; its loading and error states otherwise. */
export function Async<T>({
  resource,
  label,
  children,
}: {
  resource: Resource<T>;
  label: string;
  children: (data: T) => React.ReactNode;
}) {
  const { snapshot } = resource;
  if (snapshot.status === 'success') return <>{children(snapshot.value)}</>;
  if (snapshot.status === 'error')
    return <ErrorState error={snapshot.error} label={label} onRetry={resource.retry} />;
  return <Loading label={label} slow={snapshot.status === 'loading' && snapshot.slow} />;
}
