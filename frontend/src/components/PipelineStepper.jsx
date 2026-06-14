const STAGES = [
  { key: 'parsed', label: 'parse' },
  { key: 'awaiting_column_mapping', label: 'map columns', gate: true },
  { key: 'awaiting_clarification', label: 'clarify', gate: true },
  { key: 'ready', label: 'ready' },
  { key: 'validated', label: 'validate' },
  { key: 'modeled', label: 'build model' },
  { key: 'solved', label: 'solve' },
  { key: 'explained', label: 'explain' },
];

export default function PipelineStepper({ stage }) {
  const idx = STAGES.findIndex((s) => s.key === stage);
  return (
    <nav className="rail" aria-label="Pipeline progress">
      <ol>
        {STAGES.map((s, i) => {
          let cls = '';
          if (stage === 'failed') cls = i <= idx ? 'failed' : '';
          else if (i < idx || stage === 'explained') cls = 'done';
          else if (i === idx) cls = 'active';
          if (s.gate) cls += ' gate';
          return <li key={s.key} className={cls.trim()}>{s.label}</li>;
        })}
      </ol>
    </nav>
  );
}
