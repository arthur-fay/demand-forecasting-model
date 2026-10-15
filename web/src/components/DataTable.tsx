/** An accessible data table: a caption, column headers, numeric columns right-aligned. */
export interface Column<Row> {
  readonly key: string;
  readonly header: React.ReactNode;
  readonly cell: (row: Row) => React.ReactNode;
  readonly numeric?: boolean;
  /** The first column of each row is a row header. */
  readonly rowHeader?: boolean;
}

export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
  highlight,
}: {
  caption: string;
  columns: readonly Column<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  highlight?: (row: Row) => boolean;
}) {
  return (
    // A scrollable region must be reachable by keyboard and named.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
    <div className="table-wrap" tabIndex={0} role="region" aria-label={caption}>
      <table className="data-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={column.numeric ? 'num' : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} data-highlight={highlight?.(row) === true ? 'true' : undefined}>
              {columns.map((column) =>
                column.rowHeader ? (
                  <th key={column.key} scope="row" className={column.numeric ? 'num' : undefined}>
                    {column.cell(row)}
                  </th>
                ) : (
                  <td key={column.key} className={column.numeric ? 'num' : undefined}>
                    {column.cell(row)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
