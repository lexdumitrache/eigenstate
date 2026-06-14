import { useState } from 'react';

const DEMO_TEXT =
  'I have 3 delivery vans and 4 packages. Each van has a weight capacity (cap). ' +
  'Assign each package to exactly one van without exceeding any van\'s capacity. ' +
  'Minimize the number of vans used.';

const DEMO_VANS_CSV = 'van,cap\nv1,600\nv2,500\nv3,400\n';
const DEMO_PACKAGES_CSV = 'pkg,weight\np1,300\np2,500\np3,200\np4,150\n';

function csvFile(name, content) {
  return new File([content], name, { type: 'text/csv' });
}

function downloadCsv(name, content) {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/csv' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ProblemInput({ onSubmit, busy }) {
  const [text, setText] = useState('');
  const [files, setFiles] = useState([]);

  function handleDemo() {
    const demoFiles = [csvFile('vans.csv', DEMO_VANS_CSV), csvFile('packages.csv', DEMO_PACKAGES_CSV)];
    onSubmit(DEMO_TEXT, demoFiles);
  }

  return (
    <section className="card">
      <h2>1 · Describe the problem</h2>

      <div className="demo-banner">
        <span>New here?</span>
        <button
          className="demo-btn"
          disabled={busy}
          onClick={handleDemo}
          type="button"
        >
          Try package assignment demo →
        </button>
        <span className="demo-downloads">
          or download samples:&nbsp;
          <button
            className="link-btn"
            type="button"
            onClick={() => downloadCsv('vans.csv', DEMO_VANS_CSV)}
          >vans.csv</button>
          {' · '}
          <button
            className="link-btn"
            type="button"
            onClick={() => downloadCsv('packages.csv', DEMO_PACKAGES_CSV)}
          >packages.csv</button>
        </span>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={'Example: "I have 4 vans and 12 packages. Each van has a weight capacity. Assign packages to vans so every package is delivered and total fuel cost is minimal."'}
        aria-label="Problem description"
      />
      <div className="filebox">
        Optional data files (CSV or Excel) — vans, packages, employees, shifts…
        <input
          type="file"
          multiple
          accept=".csv,.xlsx,.xls"
          onChange={(e) => setFiles([...e.target.files])}
        />
        {files.length > 0 && (
          <div>{files.map((f) => f.name).join(' · ')}</div>
        )}
      </div>
      <button
        disabled={busy || !text.trim()}
        onClick={() => onSubmit(text, files)}
      >
        {busy ? 'Parsing…' : 'Parse problem'}
      </button>
    </section>
  );
}
