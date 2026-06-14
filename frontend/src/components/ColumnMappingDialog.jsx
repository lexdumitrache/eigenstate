import { useState } from 'react';

function MappingEditor({ mapping, onConfirm, busy }) {
  const [fields, setFields] = useState({ ...mapping.column_to_field });
  const [category, setCategory] = useState(mapping.entity_category);
  const [idCol, setIdCol] = useState(mapping.id_column || '');

  return (
    <div>
      <p>
        <strong>{mapping.file_name}</strong> — each row is one{' '}
        <input
          style={{ width: 120, fontFamily: 'var(--mono)' }}
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-label="Entity category"
        />
      </p>
      <table>
        <thead>
          <tr><th>CSV column</th><th>Maps to field</th><th>ID column</th></tr>
        </thead>
        <tbody>
          {Object.keys(fields).map((col) => (
            <tr key={col}>
              <td style={{ fontFamily: 'var(--mono)' }}>{col}</td>
              <td>
                <input
                  value={fields[col]}
                  onChange={(e) => setFields({ ...fields, [col]: e.target.value })}
                  aria-label={`Field name for ${col}`}
                />
              </td>
              <td>
                <input
                  type="radio"
                  name={`id-${mapping.file_name}`}
                  checked={idCol === col}
                  onChange={() => setIdCol(col)}
                  aria-label={`Use ${col} as ID`}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ marginTop: 12 }}>
        <button
          disabled={busy}
          onClick={() => onConfirm({
            file_name: mapping.file_name,
            column_to_field: fields,
            entity_category: category,
            id_column: idCol || null,
          })}
        >
          Confirm mapping
        </button>
      </div>
    </div>
  );
}

export default function ColumnMappingDialog({ mappings, onConfirm, busy }) {
  const pending = mappings.filter((m) => !m.confirmed);
  if (!pending.length) return null;
  return (
    <section className="card">
      <h2>2 · Confirm column mappings</h2>
      <div className="gate-banner">
        The solver will not run until you confirm how each file's columns map
        to model fields.
      </div>
      {pending.map((m) => (
        <MappingEditor key={m.file_name} mapping={m} onConfirm={onConfirm} busy={busy} />
      ))}
    </section>
  );
}
