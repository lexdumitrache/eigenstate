import { useState } from 'react';

function PairwiseTableEditor({ table, onConfirm, busy }) {
  const [agentCol, setAgentCol] = useState(table.agent_column);
  const [taskCol, setTaskCol] = useState(table.task_column);
  const [costCol, setCostCol] = useState(table.cost_column);
  const [agentCat, setAgentCat] = useState(table.agent_category);
  const [taskCat, setTaskCat] = useState(table.task_category);

  return (
    <div>
      <p>
        <strong>{table.file_name}</strong> — pairwise cost matrix
      </p>
      <table>
        <tbody>
          <tr>
            <td>Agent column</td>
            <td>
              <input
                style={{ fontFamily: 'var(--mono)' }}
                value={agentCol}
                onChange={(e) => setAgentCol(e.target.value)}
                aria-label="Agent column"
              />
            </td>
            <td>Agent category</td>
            <td>
              <input
                value={agentCat}
                onChange={(e) => setAgentCat(e.target.value)}
                aria-label="Agent category"
              />
            </td>
          </tr>
          <tr>
            <td>Task column</td>
            <td>
              <input
                style={{ fontFamily: 'var(--mono)' }}
                value={taskCol}
                onChange={(e) => setTaskCol(e.target.value)}
                aria-label="Task column"
              />
            </td>
            <td>Task category</td>
            <td>
              <input
                value={taskCat}
                onChange={(e) => setTaskCat(e.target.value)}
                aria-label="Task category"
              />
            </td>
          </tr>
          <tr>
            <td>Cost column</td>
            <td>
              <input
                style={{ fontFamily: 'var(--mono)' }}
                value={costCol}
                onChange={(e) => setCostCol(e.target.value)}
                aria-label="Cost column"
              />
            </td>
            <td colSpan={2} />
          </tr>
        </tbody>
      </table>
      <div style={{ marginTop: 12 }}>
        <button
          disabled={busy || !agentCol || !taskCol || !costCol || !agentCat || !taskCat}
          onClick={() => onConfirm({
            file_name: table.file_name,
            agent_column: agentCol,
            task_column: taskCol,
            cost_column: costCol,
            agent_category: agentCat,
            task_category: taskCat,
          })}
        >
          Confirm cost table
        </button>
      </div>
    </div>
  );
}

export default function PairwiseTableDialog({ tables, onConfirm, busy }) {
  const pending = (tables || []).filter((t) => !t.confirmed);
  if (!pending.length) return null;
  return (
    <section className="card">
      <h2>2b · Confirm pairwise cost tables</h2>
      <div className="gate-banner">
        The solver needs to know which columns identify agents, tasks, and costs
        in each cost matrix file.
      </div>
      {pending.map((t) => (
        <PairwiseTableEditor key={t.file_name} table={t} onConfirm={onConfirm} busy={busy} />
      ))}
    </section>
  );
}
