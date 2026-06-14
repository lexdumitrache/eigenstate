import { useRef, useState } from 'react';
import { api, setSessionToken } from './api';
import PipelineStepper from './components/PipelineStepper';
import ProblemInput from './components/ProblemInput';
import ColumnMappingDialog from './components/ColumnMappingDialog';
import ClarificationDialog from './components/ClarificationDialog';
import ModelViewer from './components/ModelViewer';
import ModelEditor from './components/ModelEditor';
import { ResultsPanel, ExplanationPanel } from './components/ResultsPanel';
import FeedbackPanel from './components/FeedbackPanel';

export default function App() {
  const [session, setSession] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [solving, setSolving] = useState(false);
  const solveAbortRef = useRef(null);

  async function guard(fn) {
    setBusy(true);
    setError(null);
    try {
      setSession(await fn());
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const handleParse = (text, files) => guard(async () => {
    const { session_id, session_token } = await api.createSession();
    setSessionToken(session_token);
    for (const f of files) await api.uploadFile(session_id, f);
    return api.parse(session_id, text);
  });

  const handleMapping = (mapping) =>
    guard(() => api.confirmMapping(session.session_id, mapping));

  const handleClarify = (answers) =>
    guard(() => api.clarify(session.session_id, answers));

  const handleSolve = () => {
    const controller = new AbortController();
    solveAbortRef.current = controller;
    setSolving(true);
    guard(async () => {
      try {
        return await api.solve(session.session_id, { signal: controller.signal });
      } finally {
        setSolving(false);
        solveAbortRef.current = null;
      }
    });
  };

  const handleCancelSolve = () => {
    solveAbortRef.current?.abort();
  };

  const handleFeedback = (feedback) =>
    api.submitFeedback(session.session_id, feedback);

  const handleSpecEdit = (edits) =>
    guard(() => api.editSpec(session.session_id, edits));

  const stage = session?.stage;
  const isTerminal = stage === 'cancelled' || stage === 'failed';
  const isReady = stage === 'ready';

  return (
    <div className="layout">
      <header className="masthead">
        <h1>Eigenstate</h1>
        <p>Assignment, allocation, and scheduling problems — described in plain language, solved with your assumptions confirmed.</p>
      </header>

      <PipelineStepper stage={stage || 'created'} />

      <main>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {session?.error && isTerminal && (
          <div className="error-banner" role="alert">{session.error}</div>
        )}

        <ProblemInput onSubmit={handleParse} busy={busy} />

        {session?.spec && (
          <>
            <ColumnMappingDialog
              mappings={session.spec.column_mappings}
              onConfirm={handleMapping}
              busy={busy}
            />
            {stage !== 'awaiting_column_mapping' && (
              <ClarificationDialog
                ambiguities={session.spec.ambiguities}
                onResolve={handleClarify}
                busy={busy}
              />
            )}
            <ModelViewer spec={session.spec} />
            <ModelEditor
              spec={session.spec}
              onSave={handleSpecEdit}
              busy={busy}
            />

            {isReady && (
              <section className="card" style={{ textAlign: 'center' }}>
                <p style={{ margin: '0 0 16px', color: 'var(--ink-soft)' }}>
                  All assumptions confirmed. Review the model above, then solve.
                </p>
                {solving ? (
                  <div style={{ display: 'flex', gap: '12px', justifyContent: 'center', alignItems: 'center' }}>
                    <span style={{ color: 'var(--ink-soft)' }}>Solving…</span>
                    <button
                      onClick={handleCancelSolve}
                      style={{ background: 'none', color: 'var(--ink-soft)', border: '1px solid var(--ink-soft)', padding: '6px 14px' }}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button onClick={handleSolve} disabled={busy}>
                    Confirm and solve →
                  </button>
                )}
              </section>
            )}
          </>
        )}

        {!isTerminal && (
          <>
            <ResultsPanel result={session?.result} />
            <ExplanationPanel
              explanation={session?.explanation}
              validation={session?.validation}
            />
            {stage === 'explained' && (
              <FeedbackPanel
                decisions={session?.explanation?.decisions}
                sessionId={session?.session_id}
                onSubmit={handleFeedback}
                busy={busy}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
