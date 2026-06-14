import katex from 'katex';
import 'katex/dist/katex.min.css';

function senseTeX(sense) {
  if (sense === '>=') return '\\ge';
  if (sense === '<=') return '\\le';
  return '=';
}

const CONSTRAINT_TEX = {
  capacity: (p) => {
    const cat = p.entity_category || 'agents';
    const d = p.demand_field ? `d^{\\text{${p.demand_field.replaceAll('_', '\\_')}}}` : 'd';
    const c = p.resource_field ? `C^{\\text{${p.resource_field.replaceAll('_', '\\_')}}}` : 'c';
    return `\\sum_j ${d}_j\\, x_{ij} \\le ${c}_i \\quad \\forall i \\in \\text{${cat}}`;
  },
  demand_coverage: (p) => {
    const s = senseTeX(p.sense ?? '==');
    const n = p.count ?? 1;
    return `\\sum_i x_{ij} ${s} ${n} \\quad \\forall j`;
  },
  one_per_entity: (p) => `\\sum_j x_{ij} ${senseTeX(p.sense ?? '==')} ${p.count ?? 1} \\quad \\forall i`,
  time_budget: (p) => {
    const T = p.budget_field ? `T^{\\text{${p.budget_field.replaceAll('_', '\\_')}}}` : 'T';
    const t = p.duration_field ? `t^{\\text{${p.duration_field.replaceAll('_', '\\_')}}}` : 't';
    return `\\sum_j ${t}_j\\, x_{ij} \\le ${T}_i \\quad \\forall i`;
  },
  budget_limit: (p) => `\\sum_i x_i \\le ${p.limit ?? 'B'}`,
  min_allocation: (p) => `x_i \\ge ${p.limit ?? '\\ell_i'} \\quad \\forall i`,
  max_allocation: (p) => `x_i \\le ${p.limit ?? 'u_i'} \\quad \\forall i`,
  no_overlap: () => `\\text{NoOverlap}(\\{I_t : t \\in \\text{tasks}\\})`,
  precedence: (p) => `\\text{end}(${p.before ?? 'a'}) \\le \\text{start}(${p.after ?? 'b'})`,
  compatibility: (p) => `x_{ij} = 0 \\text{ if } ${p.agent_field ?? 'f_i'} \\ne ${p.task_field ?? 'g_j'}`,
};

function Tex({ tex }) {
  return (
    <span
      className="math-block"
      dangerouslySetInnerHTML={{ __html: katex.renderToString(tex, { throwOnError: false }) }}
    />
  );
}

export default function ModelViewer({ spec }) {
  if (!spec) return null;
  const sense = spec.objective.sense === 'minimize' ? '\\min' : '\\max';
  const coef = spec.objective.coefficient_field
    ? `c^{(${spec.objective.coefficient_field.replaceAll('_', '\\_')})}_{ij}` : '';
  const objectiveTex = `${sense} \\sum ${coef} x`;

  return (
    <section className="card">
      <h2>Model · {spec.problem_type} ({spec.problem_type === 'scheduling' ? 'CP-SAT' : 'MILP'})</h2>
      <div>
        <Tex tex={objectiveTex} />
        <span style={{ color: 'var(--ink-soft)', marginLeft: 10 }}>
          {spec.objective.description}
        </span>
      </div>
      {spec.constraints.map((c) => {
        const fn = CONSTRAINT_TEX[c.constraint_type];
        return (
          <div className="constraint-row" key={c.name}>
            <span className="constraint-tag">{c.constraint_type}</span>
            {fn && <Tex tex={fn(c.parameters)} />}
            <span style={{ color: 'var(--ink-soft)', fontSize: '0.85rem' }}>
              {c.description}
            </span>
          </div>
        );
      })}
      <p className="caveat" style={{ marginBottom: 0 }}>
        {spec.entities.length} entities · spec completeness {(spec.confidence * 100).toFixed(0)}%
      </p>
    </section>
  );
}
