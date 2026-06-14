function CapacityBar({ used, capacity }) {
  if (used == null || capacity == null || capacity === 0) return null;
  const pct = Math.min(100, Math.round((used / capacity) * 100));
  const full = pct >= 100;
  return (
    <div className="group-bar" aria-label={`${pct}% utilised`}>
      <div
        className={`group-bar-fill${full ? ' full' : ''}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function GroupCard({ group }) {
  const hasUtilisation = group.used != null && group.capacity != null;
  const pct = hasUtilisation
    ? Math.min(100, Math.round((group.used / group.capacity) * 100))
    : null;
  const atCap = pct != null && pct >= 100;

  return (
    <div className={`group-card${atCap ? ' at-capacity' : ''}`}>
      <div className="group-header">
        <span className="group-agent">{group.agent_label}</span>
        <span className="group-tasks">
          {group.task_labels.length > 0
            ? group.task_labels.join(' · ')
            : <em style={{ color: 'var(--ink-soft)' }}>empty</em>}
        </span>
        {hasUtilisation && (
          <span className="group-usage">
            {group.used % 1 === 0 ? group.used : group.used.toFixed(1)}
            {' / '}
            {group.capacity % 1 === 0 ? group.capacity : group.capacity.toFixed(1)}
            {group.unit ? ` ${group.unit}` : ''}
            {atCap && <span className="at-cap-badge">full</span>}
          </span>
        )}
      </div>
      <CapacityBar used={group.used} capacity={group.capacity} />
    </div>
  );
}

export function ResultsPanel({ result }) {
  if (!result) return null;
  return (
    <section className="card">
      <h2>Result</h2>
      <p>
        <span className={`status-chip ${result.status}`}>{result.status}</span>
        {' '}<span style={{ color: 'var(--ink-soft)', fontSize: '0.85rem' }}>
          {result.solver_name} · {result.solve_time_ms} ms
        </span>
      </p>
      {result.objective_value != null && (
        <p className="objective-figure">{result.objective_value}</p>
      )}
    </section>
  );
}

export function ExplanationPanel({ explanation, validation }) {
  if (!explanation) return null;

  const hasGroups = explanation.groups && explanation.groups.length > 0;
  const hasUnassigned = explanation.unassigned && explanation.unassigned.length > 0;
  const hasBinding = explanation.binding_constraints && explanation.binding_constraints.length > 0;

  return (
    <section className="card">
      <h2>Plan</h2>
      <p style={{ color: 'var(--ink-soft)', fontSize: '0.9rem', marginBottom: hasGroups ? 16 : 8 }}>
        {explanation.summary}
      </p>

      {hasGroups && (
        <div className="groups-section">
          {explanation.groups.map((g, i) => (
            <GroupCard key={i} group={g} />
          ))}
        </div>
      )}

      {hasUnassigned && (
        <div className="unassigned-section">
          <span className="section-label">Unassigned</span>
          {explanation.unassigned.map((label, i) => (
            <span key={i} className="unassigned-chip">{label}</span>
          ))}
        </div>
      )}

      {hasBinding && (
        <div className="binding-section">
          <span className="section-label">Binding constraints</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '0.82rem', color: 'var(--ink-soft)' }}>
            {explanation.binding_constraints.join(' · ')}
          </span>
        </div>
      )}

      {!hasGroups && explanation.decisions.length > 0 && (
        <ul className="decision-list">
          {explanation.decisions.map((d, i) => <li key={i}>{d}</li>)}
        </ul>
      )}

      {explanation.caveats.map((c, i) => (
        <p className="caveat" key={i}>{c}</p>
      ))}
      {validation?.warnings?.map((w, i) => (
        <p className="caveat" key={`w${i}`}>Validation: {w}</p>
      ))}
    </section>
  );
}
