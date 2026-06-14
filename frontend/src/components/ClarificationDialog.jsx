import { useState } from 'react';

export default function ClarificationDialog({ ambiguities, onResolve, busy }) {
  const open = ambiguities.filter((a) => a.blocking && !a.resolution);
  const [answers, setAnswers] = useState({});
  if (!open.length) return null;

  const allAnswered = open.every((a) => (answers[a.id] || '').trim());

  return (
    <section className="card">
      <h2>3 · Resolve ambiguities</h2>
      <div className="gate-banner">
        These points change the model. Answer them, or proceed with stated
        assumptions — either way, the choice is yours, not the AI's.
      </div>
      {open.map((a) => (
        <div key={a.id} style={{ marginBottom: 18 }}>
          <p style={{ margin: '0 0 8px' }}>{a.question}</p>
          {a.options.map((opt) => (
            <button
              key={opt}
              className="secondary"
              style={{ marginRight: 8, marginBottom: 6 }}
              onClick={() => setAnswers({ ...answers, [a.id]: opt })}
            >
              {answers[a.id] === opt ? '✓ ' : ''}{opt}
            </button>
          ))}
          <input
            style={{ display: 'block', width: '100%', marginTop: 6, padding: 8,
                     border: '1px solid var(--grid)', borderRadius: 4 }}
            placeholder="…or type your own answer"
            value={a.options.includes(answers[a.id]) ? '' : (answers[a.id] || '')}
            onChange={(e) => setAnswers({ ...answers, [a.id]: e.target.value })}
            aria-label={`Answer for: ${a.question}`}
          />
        </div>
      ))}
      <button disabled={busy || !allAnswered} onClick={() => onResolve(answers)}>
        Submit answers
      </button>{' '}
      <button
        className="secondary"
        disabled={busy}
        onClick={() => onResolve(Object.fromEntries(
          open.map((a) => [a.id, answers[a.id] || 'proceed with my assumptions'])))}
      >
        Proceed with my assumptions
      </button>
    </section>
  );
}
