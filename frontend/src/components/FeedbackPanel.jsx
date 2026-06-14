import { useState } from 'react';

/**
 * Post-solve feedback collection.
 *
 * Shows after EXPLAINED stage. Asks whether the plan was accepted and, if not,
 * which decisions the user changed and why. Submits to /api/sessions/{id}/feedback.
 */
export default function FeedbackPanel({ decisions, sessionId, onSubmit, busy }) {
  const [acceptance, setAcceptance] = useState(null); // 'yes' | 'changed' | 'no'
  const [changedRows, setChangedRows] = useState({}); // index -> { userChange, reason }
  const [submitted, setSubmitted] = useState(false);
  const [inferredPrefs, setInferredPrefs] = useState([]);

  if (!decisions || decisions.length === 0) return null;
  if (submitted) {
    return (
      <section className="card feedback-card">
        <h2>Feedback recorded</h2>
        <p style={{ color: 'var(--ink-soft)' }}>
          Thanks — we'll remember this feedback and surface it in future explanations.
        </p>
        {inferredPrefs.length > 0 && (
          <>
            <p style={{ marginTop: '12px', fontWeight: 600 }}>Noted preferences (used in explanations):</p>
            <ul className="decision-list">
              {inferredPrefs.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          </>
        )}
      </section>
    );
  }

  function toggleDecision(i) {
    setChangedRows(prev => {
      const next = { ...prev };
      if (next[i]) {
        delete next[i];
      } else {
        next[i] = { userChange: '', reason: '' };
      }
      return next;
    });
  }

  function updateChange(i, field, value) {
    setChangedRows(prev => ({
      ...prev,
      [i]: { ...prev[i], [field]: value },
    }));
  }

  async function handleSubmit() {
    const changes = Object.entries(changedRows)
      .filter(([, v]) => v.userChange.trim())
      .map(([i, v]) => ({
        original_decision: decisions[parseInt(i)],
        user_change: v.userChange.trim(),
        reason: v.reason.trim(),
      }));

    const result = await onSubmit({
      accepted: acceptance === 'yes',
      changes,
    });

    setInferredPrefs(result?.inferred_preferences || []);
    setSubmitted(true);
  }

  const canSubmit = acceptance !== null && (
    acceptance !== 'changed' ||
    Object.values(changedRows).some(v => v.userChange.trim())
  );

  return (
    <section className="card feedback-card">
      <h2>Was this plan helpful?</h2>
      <p style={{ color: 'var(--ink-soft)', marginBottom: '16px' }}>
        Your feedback is stored and surfaced in future explanations. It does not change the optimizer.
      </p>

      <div className="feedback-acceptance">
        {[
          { value: 'yes', label: 'Yes — used as-is' },
          { value: 'changed', label: 'Mostly, but I changed some assignments' },
          { value: 'no', label: 'No — discarded' },
        ].map(opt => (
          <label key={opt.value} className="feedback-radio">
            <input
              type="radio"
              name="acceptance"
              value={opt.value}
              checked={acceptance === opt.value}
              onChange={() => {
                setAcceptance(opt.value);
                if (opt.value !== 'changed') setChangedRows({});
              }}
            />
            {opt.label}
          </label>
        ))}
      </div>

      {acceptance === 'changed' && (
        <div className="feedback-decisions">
          <p style={{ marginTop: '16px', marginBottom: '8px', fontWeight: 600 }}>
            Which assignments did you change?
          </p>
          {decisions.map((d, i) => (
            <div key={i} className="feedback-decision-row">
              <label className="feedback-checkbox">
                <input
                  type="checkbox"
                  checked={!!changedRows[i]}
                  onChange={() => toggleDecision(i)}
                />
                <span className="feedback-decision-text">{d}</span>
              </label>
              {changedRows[i] && (
                <div className="feedback-change-inputs">
                  <input
                    type="text"
                    placeholder="What did you assign instead?"
                    value={changedRows[i].userChange}
                    onChange={e => updateChange(i, 'userChange', e.target.value)}
                  />
                  <input
                    type="text"
                    placeholder="Why? (optional)"
                    value={changedRows[i].reason}
                    onChange={e => updateChange(i, 'reason', e.target.value)}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <button
        style={{ marginTop: '20px' }}
        onClick={handleSubmit}
        disabled={busy || !canSubmit}
      >
        {busy ? 'Saving…' : 'Submit feedback'}
      </button>
    </section>
  );
}
