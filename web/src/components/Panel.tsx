import { useId } from 'react';

/** A section answering one question: title, what is plotted (with units), status, content. */
export function Panel({
  title,
  subtitle,
  tags,
  actions,
  children,
  headingLevel = 2,
  id,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  tags?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  headingLevel?: 2 | 3;
  id?: string;
}) {
  const generated = useId();
  const headingId = id ?? `panel-${generated}`;
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <section className="panel" aria-labelledby={headingId}>
      <div className="panel-header">
        <div className="panel-title">
          <Heading id={headingId}>{title}</Heading>
          {subtitle !== undefined && <p className="subtitle">{subtitle}</p>}
          {tags}
        </div>
        {actions !== undefined && <div className="panel-actions">{actions}</div>}
      </div>
      {children}
    </section>
  );
}
