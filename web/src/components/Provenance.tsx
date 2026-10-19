/**
 * Where the numbers come from: the API's provenance block, shown with the data it
 * describes (repository path, run id, fingerprints, file hashes) and the integrity
 * notices the API sends with them.
 */
import type { Integrity, Provenance as ProvenanceBlock, Source } from '../api/schemas';
import { shortHash } from '../lib/format';
import { ORIGIN_TEXT } from '../profile';

function SourceCard({ source }: { source: Source }) {
  const files = Object.entries(source.files);
  return (
    <div className="provenance-source">
      <dl>
        <dt>Source</dt>
        <dd>{source.kind}</dd>
        <dt>Path</dt>
        <dd>{source.path}</dd>
        {source.run_id !== null && (
          <>
            <dt>Run</dt>
            <dd>{source.run_id}</dd>
          </>
        )}
        {source.result_fingerprint !== null && (
          <>
            <dt>Result fingerprint</dt>
            <dd>{source.result_fingerprint}</dd>
          </>
        )}
        {source.config_fingerprint !== null && (
          <>
            <dt>Config fingerprint</dt>
            <dd>{source.config_fingerprint}</dd>
          </>
        )}
        {files.map(([name, sha]) => (
          <FileRow key={name} name={name} sha={sha} />
        ))}
      </dl>
    </div>
  );
}

function FileRow({ name, sha }: { name: string; sha: string }) {
  return (
    <>
      <dt>{name}</dt>
      <dd title={sha}>sha256 {sha}</dd>
    </>
  );
}

/** One line of provenance under a chart, with every source behind a disclosure. */
export function ProvenanceFooter({ provenance }: { provenance: ProvenanceBlock }) {
  const primary = provenance.sources[0];
  return (
    <details className="disclosure">
      <summary>
        Sources and fingerprints
        <span className="provenance-line">
          {primary ? primary.path : 'no source'} · ATLAS {provenance.atlas_version} ·{' '}
          {provenance.snapshot_id}{' '}
          <span className="mono">{shortHash(provenance.data_fingerprint, 8)}</span>
        </span>
      </summary>
      <div className="disclosure-body provenance">
        <p className="provenance-line">
          API {provenance.api_version} · ATLAS {provenance.atlas_version} · snapshot{' '}
          {provenance.snapshot_id} · data fingerprint{' '}
          <span className="mono">{provenance.data_fingerprint ?? 'unavailable'}</span>.{' '}
          {ORIGIN_TEXT.fileHashes}
        </p>
        {provenance.sources.map((source) => (
          <SourceCard key={`${source.kind}:${source.path}`} source={source} />
        ))}
      </div>
    </details>
  );
}

/** The labels the API sends with these numbers, verbatim and always visible. */
export function Notices({
  integrity,
  title = 'What these numbers are',
}: {
  integrity: Integrity;
  title?: string;
}) {
  if (integrity.notices.length === 0) return null;
  return (
    <div className="stack-2 small">
      <p className="secondary">
        <strong>{title}</strong>{' '}
        <span className="muted">(labels sent by the API with this data)</span>
      </p>
      <ul className="notice-list">
        {integrity.notices.map((notice) => (
          <li key={notice}>{notice}</li>
        ))}
      </ul>
    </div>
  );
}

/** Notices and provenance together: the standard foot of a data panel. */
export function Evidence({
  integrity,
  provenance,
  title,
}: {
  integrity: Integrity;
  provenance: ProvenanceBlock;
  /** The notices' heading, when a panel shows the evidence of more than one answer. */
  title?: string;
}) {
  return (
    <div className="stack-3">
      <Notices integrity={integrity} {...(title !== undefined ? { title } : {})} />
      <ProvenanceFooter provenance={provenance} />
    </div>
  );
}
