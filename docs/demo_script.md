# Demo Script — Eigenstate v1

This script walks through the three supported problem families end-to-end. Use it for live demos, screencasts, or manual QA.

---

## Setup

```bash
# Terminal 1 — backend
cd "/Users/alexandradumitrache/Downloads/eigenstate 2/backend"
export ANTHROPIC_API_KEY=sk-...
export EIGENSTATE_LLM=anthropic
uvicorn api.main:app --reload --port 8000

# Terminal 2 — frontend
cd "/Users/alexandradumitrache/Downloads/eigenstate 2/frontend"
npm install && npm run dev   # http://localhost:5173
```

Open http://localhost:5173 in a browser.

---

## Demo 1 — Assignment (workers ↔ tasks)

**What to show:** plain-language input, clarification gate, solve, explanation.

1. Click **New session**.
2. Enter:
   > "I have 4 engineers — Alice, Bob, Carol, Dan. There are 5 support tickets. Each engineer can handle at most 2 tickets. Alice is unavailable for ticket 3. Minimize total unresolved tickets."
3. Click **Parse**.
4. The system detects one ambiguity: *"Should every ticket be assigned, or can some go unresolved?"*
5. Answer: *"Every ticket must be assigned."*
6. Click **Solve**.
7. Walk through the result table and the natural-language explanation.

**Key talking points:**
- The solve button was disabled until the clarification was answered — enforced server-side (409 if bypassed).
- No LP syntax was written. The LLM emitted `max_assignments_per_agent: 2` and `availability` constraints.

---

## Demo 2 — Allocation (budget split)

**What to show:** CSV upload, column mapping, continuous LP.

1. Click **New session**.
2. Upload `examples/packages.csv`.
3. Enter:
   > "Allocate a $50,000 marketing budget across these channels. Maximize expected reach. No single channel gets more than 40% of the budget."
4. Click **Parse**.
5. The column mapper presents detected headers — confirm mapping (channel → agent, reach → objective coefficient, min_spend / max_spend → bounds).
6. No ambiguities — **Solve** is immediately available.
7. Show the allocation percentages and the explanation.

**Key talking points:**
- Single-category allocation compiles to a continuous LP, not MILP — faster and always optimal.
- The 40% cap became a `budget` constraint with an upper bound of 0.4 × total.

---

## Demo 3 — Scheduling (shifts)

**What to show:** multi-constraint scheduling, OR-Tools CP-SAT.

1. Click **New session**.
2. Enter:
   > "Schedule 6 nurses across 3 daily shifts (morning, afternoon, night) for the next 7 days. Each shift needs at least 2 nurses. Each nurse works at most 5 shifts per week and never two consecutive night shifts."
3. Click **Parse**.
4. The system surfaces an ambiguity: *"Can a nurse work morning then night on the same day?"*
5. Answer: *"No — only one shift per day per nurse."*
6. Click **Solve**.
7. Show the 7-day schedule grid and the explanation.

**Key talking points:**
- CP-SAT handles no-overlap and precedence natively.
- The "no consecutive nights" constraint is a `precedence`-style constraint on the shift sequence.

---

## Demo 4 — Out-of-scope detection (routing)

**What to show:** graceful refusal, scope honesty.

1. Click **New session**.
2. Enter:
   > "I have 3 delivery vans and 20 stops. Find the shortest route for each van."
3. Click **Parse**.
4. The system refuses: *"This is a vehicle routing problem (VRP). Eigenstate v1 optimises which van handles which stops, not the order of stops. VRP support is planned for v2."*

**Key talking point:** The model knows what it cannot do and says so explicitly rather than producing a wrong answer.

---

## Demo 5 — Feedback loop

**What to show:** preference-aware explanations across sessions.

1. Complete Demo 1.
2. On the result screen, click **Give feedback**.
3. Reject one assignment (e.g. move ticket 3 from Bob to Carol) and enter reason: *"Carol has domain expertise in ticket 3's area."*
4. Submit.
5. Run a new similar assignment session.
6. Note that the explanation on the new solve mentions the stored preference context.

**Key talking point:** Past corrections surface in future explanations — the optimizer itself doesn't change, but explanations become more contextually relevant.

---

## Offline / no-API-key demo

The test suite runs fully offline using a `DeterministicStub` adapter:

```bash
cd "/Users/alexandradumitrache/Downloads/eigenstate 2/backend"
python -m pytest tests/ -v
```

Seven E2E tests cover all three problem families, the file-upload + column-mapping flow, clarification gate enforcement, routing refusal, and the infeasibility pre-check.
