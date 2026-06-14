import { useState } from 'react';

const PROBLEM_TYPES = ['assignment', 'allocation', 'scheduling'];
const SENSES = ['minimize', 'maximize'];
const CONSTRAINT_SENSES = ['==', '>=', '<='];

// Parameters that have a numeric "limit" concept per constraint type
const NUMERIC_PARAMS = {
  capacity: [],
  demand_coverage: ['count'],
  one_per_entity: ['count'],
  time_budget: [],
  budget_limit: ['limit'],
  min_allocation: ['limit'],
  max_allocation: ['limit'],
  precedence: [],
  no_overlap: [],
  compatibility: [],
};

function ConstraintEditor({ constraint, onChange }) {
  const params = constraint.parameters || {};
  const numericKeys = NUMERIC_PARAMS[constraint.constraint_type] ?? [];
  const hasSense = 'sense' in params;

  return (
    <div className="me-constraint-row">
      <span className="constraint-tag">{constraint.constraint_type}</span>
      <span className="me-constraint-name">{constraint.name}</span>
      {hasSense && (
        <select
          value={params.sense ?? '=='}
          aria-label={`Sense for ${constraint.name}`}
          onChange={(e) => onChange({ parameters: { ...params, sense: e.target.value } })}
        >
          {CONSTRAINT_SENSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      )}
      {numericKeys.map((key) => (
        <label key={key} className="me-param-label">
          <span>{key}</span>
          <input
            type="number"
            style={{ width: 72 }}
            value={params[key] ?? ''}
            aria-label={`${key} for ${constraint.name}`}
            onChange={(e) => {
              const val = e.target.value === '' ? undefined : Number(e.target.value);
              const next = { ...params };
              if (val === undefined) delete next[key]; else next[key] = val;
              onChange({ parameters: next });
            }}
          />
        </label>
      ))}
      <span className="me-desc">{constraint.description}</span>
    </div>
  );
}

export default function ModelEditor({ spec, onSave, busy }) {
  const [open, setOpen] = useState(false);
  const [problemType, setProblemType] = useState(spec.problem_type);
  const [objSense, setObjSense] = useState(spec.objective.sense);
  const [coefField, setCoefField] = useState(spec.objective.coefficient_field ?? '');
  const [constraints, setConstraints] = useState(
    spec.constraints.map((c) => ({ ...c, parameters: { ...c.parameters } }))
  );
  const [saved, setSaved] = useState(false);

  function patchConstraint(idx, patch) {
    const next = constraints.map((c, i) => i === idx ? { ...c, ...patch } : c);
    setConstraints(next);
    setSaved(false);
  }

  function handleSave() {
    setSaved(false);
    onSave({
      problem_type: problemType,
      objective_sense: objSense,
      objective_coefficient_field: coefField || null,
      constraint_patches: constraints.map((c) => ({
        name: c.name,
        parameters: c.parameters,
      })),
    }).then(() => setSaved(true));
  }

  return (
    <section className="card me-card">
      <button
        className="me-toggle secondary"
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? '▾' : '▸'} Edit extracted model
      </button>

      {open && (
        <div className="me-body">
          <div className="me-row">
            <label className="me-field-label">Problem type</label>
            <select value={problemType} onChange={(e) => { setProblemType(e.target.value); setSaved(false); }}>
              {PROBLEM_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          <div className="me-row">
            <label className="me-field-label">Objective</label>
            <div className="me-radios">
              {SENSES.map((s) => (
                <label key={s} className="me-radio-label">
                  <input
                    type="radio"
                    name="obj-sense"
                    value={s}
                    checked={objSense === s}
                    onChange={() => { setObjSense(s); setSaved(false); }}
                  />
                  {s}
                </label>
              ))}
            </div>
            <input
              style={{ marginLeft: 12, width: 160 }}
              value={coefField}
              placeholder="coefficient field (optional)"
              aria-label="Objective coefficient field"
              onChange={(e) => { setCoefField(e.target.value); setSaved(false); }}
            />
          </div>

          {constraints.length > 0 && (
            <div className="me-constraints">
              <p className="me-section-label">Constraints</p>
              {constraints.map((c, i) => (
                <ConstraintEditor
                  key={c.name}
                  constraint={c}
                  onChange={(patch) => patchConstraint(i, patch)}
                />
              ))}
            </div>
          )}

          <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
            <button onClick={handleSave} disabled={busy} type="button">
              Apply changes
            </button>
            {saved && <span style={{ color: 'var(--green)', fontSize: '0.88rem' }}>✓ Saved</span>}
          </div>
        </div>
      )}
    </section>
  );
}
