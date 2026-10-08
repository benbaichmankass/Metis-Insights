# World Event Prediction Engine

**Status:** Draft v0.1  
**Owner:** Independent Analyst Project  
**Last updated:** 2026-10-08  
**Purpose:** Build an evidence-driven system that produces, evaluates, and explains probabilistic forecasts about well-defined world events.

## 1. Executive Summary

The World Event Prediction Engine (WEPE) is an open-source-intelligence and forecasting platform. It transforms public structured and unstructured information into auditable probability forecasts for defined questions in economic, geopolitical, social, scientific, technological, environmental, and public-policy domains.

Prediction markets are used initially as a question-discovery source, a live benchmark, and a resolution dataset. They are not assumed to be ground truth before resolution, and market prices must be treated as one input signal among many.

The initial product is a **research and forecasting system**, not an automated trading system. Trading integrations, if ever considered, should be a separate, risk-controlled product boundary.

## 2. Goals

### Primary goals

- Create a repeatable framework for turning ambiguous world developments into measurable forecasting questions.
- Ingest open-source structured data, documents, news, and market signals with provenance.
- Produce calibrated probabilistic forecasts, rather than binary claims or unsupported narratives.
- Compare models against simple baselines and prediction-market probabilities at the same forecast timestamp.
- Retain evidence, source quality, model version, features, and reasoning artifacts for each forecast.
- Support historical backtesting, live monitoring, and post-resolution learning.

### Non-goals for v0

- Autonomous trading or placing bets.
- Predicting unbounded or poorly specified questions.
- Treating social-media sentiment as verified fact.
- Replacing subject-matter review in high-stakes domains.
- Building a universal model that forecasts every event type equally well.

## 3. Product Principles

1. **Questions before models.** Every forecast begins with a precisely resolvable question.
2. **Probabilities, not certainty.** Outputs are probabilities with time horizon and calibration metadata.
3. **Point-in-time integrity.** A forecast may use only information available before its timestamp.
4. **Evidence with provenance.** Every derived claim must trace to stored source material and extraction steps.
5. **Market price is a baseline, not truth.** It is a useful aggregate signal that may be biased, illiquid, or stale.
6. **Simple models earn their replacement.** New complexity must improve out-of-sample performance.
7. **Human review for ontology and resolution.** Automation assists; it does not silently define targets.

## 4. Users and Use Cases

### Users

- Independent analysts and researchers.
- Domain specialists contributing evidence or forecasts.
- Editors/reviewers validating questions, source quality, and resolution rules.
- Developers operating data and model pipelines.

### Initial use cases

| Use case | Example question | Forecast horizon | Primary inputs |
|---|---|---:|---|
| Monetary policy | “Will the Fed target rate be cut by at least 25 bps at the next scheduled meeting?” | Days to months | Policy statements, futures, macro releases, market prices |
| Elections | “Will candidate X win office Y under the stated electoral authority’s result?” | Weeks to months | Poll aggregates, official filings, news, market prices |
| Geopolitical escalation | “Will event X occur in region Y before date Z, under explicit criteria?” | Days to months | Official statements, conflict datasets, event reporting, market prices |
| Macroeconomic release | “Will monthly CPI exceed threshold T?” | Weeks | Historical releases, consensus, commodities, news |
| Science and technology | “Will agency/regulator approve product X by date Z?” | Weeks to quarters | Trial registries, agency calendars, company filings, specialist reporting |

## 5. Forecasting Question Contract

A forecastable question must be represented as a versioned contract. This avoids target leakage, hindsight edits, and ambiguous scoring.

```yaml
question_id: q_2026_000001
version: 1
title: "Will [observable outcome] occur by [deadline]?"
domain: geopolitical
outcome_type: binary            # binary | categorical | numeric | ordinal
resolution_criteria: |
  Explicit test for YES/NO or outcome labels, including authoritative sources.
resolution_authority:
  - name: "Named official source or predefined evidence hierarchy"
resolution_deadline: "2026-12-31T23:59:59Z"
forecast_cutoff: "2026-10-08T12:00:00Z"
forecast_horizons: ["7d", "30d", "90d"]
geography: ["country_or_region_id"]
entities: ["entity_id_1", "entity_id_2"]
market_links: ["market_id_1"]
status: active                 # draft | active | frozen | resolved | voided
```

### Question quality checks

- The outcome must be observable and independently resolvable.
- The deadline and time zone must be explicit.
- The data cutoff must precede the forecast timestamp.
- The resolution source hierarchy must be stated before forecasting.
- The wording must not contain undefined terms such as “major,” “successful,” or “significant” unless thresholds are provided.
- A question version must be frozen once forecasts are published.

## 6. System Architecture

```text
                         +--------------------------+
                         | Question Registry        |
                         | contracts + resolutions  |
                         +------------+-------------+
                                      |
                                      v
+----------------+        +--------------------------+       +-------------------+
| External data  | -----> | Ingestion and provenance | ----> | Raw object store  |
| APIs / feeds / |        | scheduling, hash, time   |       | documents, JSON   |
| documents      |        +------------+-------------+       +-------------------+
+----------------+                     |
                                      v
                         +--------------------------+
                         | Normalization / extraction|
                         | entities, events, metrics |
                         +------+---------------+---+
                                |               |
                                v               v
                     +---------------+   +------------------+
                     | Relational DB |   | Search / vectors  |
                     | facts, labels |   | retrieval corpus  |
                     +-------+-------+   +---------+--------+
                             |                     |
                             +----------+----------+
                                        v
                         +--------------------------+
                         | Feature store             |
                         | point-in-time snapshots   |
                         +------------+-------------+
                                      |
                                      v
                         +--------------------------+
                         | Forecast service          |
                         | baselines + ensembles     |
                         +------------+-------------+
                                      |
                       +--------------+---------------+
                       v                              v
          +-------------------------+    +-------------------------+
          | Evaluation / calibration |    | API + analyst dashboard |
          | backtests, scorecards    |    | forecast + evidence     |
          +-------------------------+    +-------------------------+
```

## 7. Data Sources

### Source tiers

| Tier | Source class | Examples | Typical role |
|---|---|---|---|
| 1 | Primary official data | Statistical agencies, central banks, election authorities, regulators | Resolution and high-confidence facts |
| 2 | Original institutional publications | Company filings, research institutions, multilateral organizations | Context and leading indicators |
| 3 | Reputable reporting | Wire services, specialist journalism, established local outlets | Event detection and corroboration |
| 4 | Market-derived signals | Prediction markets, futures, options, surveys | Benchmark, belief signal, question discovery |
| 5 | Public discourse | Social platforms, blogs, forums | Weak/early signal; never sole confirmation |

### Initial connectors

- Prediction-market catalog, prices, volume, liquidity, question text, and resolution status.
- Macroeconomic APIs and official releases.
- GDELT or equivalent event/news metadata feed.
- Curated RSS feeds from primary institutions and reputable publishers.
- Government/regulatory calendars and official document repositories.

### Data governance requirements

- Retain original payload or document where licensing permits; otherwise store permitted metadata and a stable URL.
- Record collection time, publication time, access method, source identifier, content hash, and parser version.
- Respect source terms, robots controls, rate limits, copyright, and API licenses.
- Keep personally sensitive data out of v0 unless a documented legal and ethical review permits it.

## 8. Canonical Data Model

PostgreSQL is the system of record for versioned transactional data. Object storage retains raw artifacts. A vector index supports retrieval; a graph layer is optional until relationship queries justify it.

```sql
-- Forecast question and resolution
CREATE TABLE questions (
  question_id UUID PRIMARY KEY,
  version INTEGER NOT NULL,
  title TEXT NOT NULL,
  domain TEXT NOT NULL,
  outcome_type TEXT NOT NULL,
  resolution_criteria TEXT NOT NULL,
  resolution_deadline TIMESTAMPTZ NOT NULL,
  forecast_cutoff TIMESTAMPTZ,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(question_id, version)
);

CREATE TABLE resolutions (
  resolution_id UUID PRIMARY KEY,
  question_id UUID NOT NULL,
  question_version INTEGER NOT NULL,
  outcome_json JSONB NOT NULL,
  resolved_at TIMESTAMPTZ NOT NULL,
  authority_name TEXT NOT NULL,
  evidence_ids UUID[] NOT NULL,
  reviewer_id TEXT,
  notes TEXT
);

-- External source artifacts and derived claims
CREATE TABLE source_artifacts (
  artifact_id UUID PRIMARY KEY,
  source_id TEXT NOT NULL,
  url TEXT,
  published_at TIMESTAMPTZ,
  collected_at TIMESTAMPTZ NOT NULL,
  content_hash TEXT NOT NULL,
  media_type TEXT,
  language TEXT,
  raw_object_uri TEXT,
  license_notes TEXT,
  extraction_version TEXT
);

CREATE TABLE evidence_claims (
  claim_id UUID PRIMARY KEY,
  artifact_id UUID NOT NULL,
  question_id UUID,
  claim_text TEXT NOT NULL,
  entity_ids JSONB,
  event_time_start TIMESTAMPTZ,
  event_time_end TIMESTAMPTZ,
  confidence NUMERIC,
  source_tier SMALLINT,
  extracted_at TIMESTAMPTZ NOT NULL,
  human_verified BOOLEAN DEFAULT FALSE
);

-- Market snapshots
CREATE TABLE markets (
  market_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  question_text TEXT NOT NULL,
  resolution_rules TEXT,
  closes_at TIMESTAMPTZ,
  resolves_at TIMESTAMPTZ,
  status TEXT
);

CREATE TABLE market_snapshots (
  market_id TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  outcome_probabilities JSONB NOT NULL,
  volume NUMERIC,
  liquidity NUMERIC,
  open_interest NUMERIC,
  PRIMARY KEY (market_id, observed_at)
);

-- Point-in-time features and forecasts
CREATE TABLE feature_snapshots (
  feature_snapshot_id UUID PRIMARY KEY,
  question_id UUID NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  feature_set_version TEXT NOT NULL,
  values_json JSONB NOT NULL,
  provenance_json JSONB NOT NULL
);

CREATE TABLE forecasts (
  forecast_id UUID PRIMARY KEY,
  question_id UUID NOT NULL,
  question_version INTEGER NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  horizon TEXT NOT NULL,
  model_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  probabilities JSONB NOT NULL,
  uncertainty_json JSONB,
  feature_snapshot_id UUID,
  explanation_uri TEXT,
  published_at TIMESTAMPTZ NOT NULL
);
```

## 9. Ingestion and Processing

### Ingestion pipeline

1. **Discover:** Poll APIs, RSS feeds, registries, and curated source lists on a source-specific schedule.
2. **Acquire:** Fetch data with rate limits, retry policy, source credentials, and immutable collection timestamp.
3. **Preserve:** Store raw payload/document plus content hash and provenance metadata.
4. **Normalize:** Convert data to canonical formats, normalize time zones, currencies, units, language metadata, and identifiers.
5. **Extract:** Identify entities, relations, events, quantities, dates, topics, and claims.
6. **Link:** Map artifacts and claims to canonical entities and forecasting questions.
7. **Quality-check:** Detect duplicates, malformed fields, missing dates, extraction failures, and unusual volume shifts.

### Unstructured-text processing

The text pipeline should produce structured claims, not merely sentiment labels.

```text
Document -> language detection -> deduplication -> entity linking
         -> event/claim extraction -> source-quality assessment
         -> embeddings + indexed chunks -> question relevance scoring
         -> human review queue when confidence or impact requires it
```

Store the evidence span, source URL, publication timestamp, extraction-model version, and confidence score for each claim. A generated narrative must cite retrieved evidence and distinguish facts, model inferences, and uncertainties.

## 10. Feature Framework

All features must be computed from a point-in-time snapshot. Feature definitions, transformations, availability lag, and provenance must be version-controlled.

| Feature family | Examples | Notes |
|---|---|---|
| Market | Implied probabilities, spreads, volume, liquidity, price momentum | Use as benchmark and input; account for liquidity and stale prices |
| Macro/financial | Inflation, yields, FX, commodity prices, employment releases | Record release schedules and revision vintages |
| Events | Counts/severity of protests, conflict incidents, policy actions | Require clear event taxonomy and geography |
| Textual evidence | Claim volume, source-weighted sentiment, novelty, topic shift | Do not equate sentiment with truth |
| Network/actors | Official statements, alliance/trade relations, organizational ties | Use only explainable, stable relationships in v0 |
| Seasonal/calendar | Election timetable, policy meeting, reporting deadline, holiday effects | Easy baseline features; useful for operational timing |

## 11. Modeling Strategy

### Model ladder

Start with models that are easy to audit and difficult to overfit. Promote complexity only after robust out-of-sample gains.

| Stage | Model | Role |
|---|---|---|
| 0 | Constant/base-rate forecast | Minimum benchmark |
| 1 | Prediction-market snapshot | External benchmark at matched timestamp |
| 2 | Regularized logistic/linear models | Transparent tabular baseline |
| 3 | Gradient-boosted trees | Nonlinear structured-data model |
| 4 | Text-derived features plus structured ensemble | Multimodal practical model |
| 5 | Temporal/deep models | Only for domains with enough aligned history |
| 6 | Bayesian/stacked ensemble | Combine calibrated component forecasts |

### Output contract

Every published forecast contains:

```json
{
  "question_id": "q_2026_000001",
  "as_of": "2026-10-08T12:00:00Z",
  "horizon": "30d",
  "outcomes": {"yes": 0.62, "no": 0.38},
  "uncertainty": {"method": "bootstrap_or_conformal", "interval": [0.51, 0.72]},
  "model": {"name": "ensemble", "version": "0.3.1"},
  "baselines": {"market_probability": 0.58, "base_rate": 0.43},
  "top_evidence_ids": ["..."],
  "caveats": ["Low market liquidity", "Sparse historical analogs"]
}
```

## 12. Evaluation and Backtesting

### Rules

- Split data chronologically, never randomly, for time-dependent questions.
- Reconstruct data availability as of each forecast time; do not use revised or subsequently published data unless it was available then.
- Evaluate by domain, horizon, geography, outcome prevalence, and source regime.
- Compare every model with base rate and market-price benchmarks at identical timestamps.
- Report uncertainty around performance metrics because resolved samples may be small.

### Metrics

| Metric | What it measures | Use |
|---|---|---|
| Brier score | Squared error of probability forecasts | Primary binary-outcome score |
| Log loss | Penalizes confident, incorrect forecasts | Detects overconfidence |
| Calibration curve / ECE | Whether stated probabilities match empirical frequency | Reliability evaluation |
| ROC-AUC / PR-AUC | Ranking quality | Secondary; not sufficient alone |
| Sharpness | Concentration of forecasts away from base rate | Useful only alongside calibration |
| Lead-time value | Accuracy by time remaining to resolution | Determines operational usefulness |

### Resolution workflow

1. Freeze the question version and forecast record.
2. Gather predefined authoritative evidence.
3. Assign outcome according to pre-registered criteria.
4. Require a second reviewer for ambiguous or material resolutions.
5. Mark void when criteria cannot be satisfied; do not score void questions.
6. Recompute all model and benchmark scores reproducibly.

## 13. Human-in-the-Loop Operations

### Review queues

- New questions: ambiguity, feasibility, resolution authority, deadlines.
- Entity matching: ambiguous actors, places, organizations, and aliases.
- High-impact claims: extraction confidence below threshold or conflicting high-tier sources.
- Forecast publication: anomalous probability changes, sparse evidence, or large model-market divergence.
- Resolution: any disputed, ambiguous, or politically sensitive outcome.

### Analyst interface

A forecast detail page should show:

- Question wording, version, deadline, and resolution rule.
- Current model probability, prior forecasts, and prediction-market benchmark.
- Evidence timeline with source tier and publication time.
- Feature-change summary and model version.
- Known caveats, missing data, and conflict indicators.
- Exportable forecast/evidence record for research reproducibility.

## 14. Security, Ethics, and Risk Controls

- Enforce API-key management, least privilege, encrypted storage, and audit logs.
- Apply rate limits and source-compliance checks to collectors.
- Separate raw source content from derived outputs and maintain deletion/retention policies.
- Do not target individuals, infer sensitive attributes, or facilitate surveillance.
- Label speculative output clearly; avoid language that implies certainty.
- Establish an abuse policy for election misinformation, conflict misinformation, market manipulation, and other high-impact misuse.
- Maintain model cards and incident logs documenting failure modes, data gaps, and observed biases.

## 15. Suggested v0 Tech Stack

| Concern | Recommended v0 choice | Why |
|---|---|---|
| Orchestration | Prefect or Airflow | Scheduled, observable ingestion and feature jobs |
| API | FastAPI | Typed Python services and OpenAPI support |
| Transactional storage | PostgreSQL + TimescaleDB extension if needed | Strong relational integrity and time-series support |
| Raw storage | S3-compatible object storage | Immutable artifacts and low-cost retention |
| Search | OpenSearch/Elasticsearch | Lexical evidence retrieval and filtering |
| Vector retrieval | pgvector first | Simpler operations; defer dedicated vector DB |
| ML/experiments | Python, scikit-learn, PyTorch, MLflow | Fast baselines plus experiment tracking |
| Data quality | Great Expectations or dbt tests | Observable pipeline contracts |
| Dashboard | Streamlit for prototype; React later | Rapid internal analyst workflow |
| Deployment | Docker + managed container service | Low operational overhead for v0 |

## 16. Repository Layout

```text
prediction-engine/
  docs/
    design/
    question-contracts/
    model-cards/
  services/
    ingestion/
    extraction/
    feature-service/
    forecast-api/
  pipelines/
    sources/
    features/
    backtests/
  packages/
    schemas/
    evaluation/
    common/
  data-contracts/
  infra/
  tests/
  notebooks/
```

## 17. Delivery Roadmap

### Milestone 0: Research contract (1-2 weeks)

- Select one narrow domain: for example, central-bank decisions or a subset of regulated political/economic questions.
- Write 20–50 forecasting question contracts with explicit resolution rules.
- Identify legal/technical access methods and retention rules for each source.
- Define success criteria: calibration, score improvement over baselines, forecast lead time, and analyst review burden.

### Milestone 1: Data and ledger (2-4 weeks)

- Implement question registry, raw artifact store, source provenance, and market snapshots.
- Add one prediction-market connector, one official structured-data source, and one news/event source.
- Build a minimal analyst page for reviewing questions and evidence.
- Produce reproducible historical snapshots for a small resolved-question set.

### Milestone 2: Baseline forecasting (2-4 weeks)

- Implement base-rate, market-price, and regularized structured-feature baselines.
- Build time-aware backtesting and evaluation reports.
- Publish an internal scorecard segmented by domain and horizon.
- Add calibration methods such as isotonic regression or Platt scaling, selected using validation data only.

### Milestone 3: Evidence-aware forecasting (4-6 weeks)

- Implement document ingestion, event/claim extraction, retrieval, and evidence timelines.
- Add text-derived features and a structured-plus-text ensemble.
- Add reviewer workflows for high-impact and conflicting evidence.
- Track model versions, feature versions, and data snapshots in every forecast.

### Milestone 4: Operational hardening (ongoing)

- Monitoring, alerts, source health, drift checks, incident playbooks, and access control.
- Add domains only after demonstrating stable calibration and usable data availability in the initial domain.
- Consider advanced temporal/deep models only when data volume and backtests justify them.

## 18. MVP Acceptance Criteria

The MVP is complete when it can:

- Manage at least 25 active, versioned, resolvable questions in one domain.
- Ingest and preserve point-in-time snapshots from at least three heterogeneous public sources.
- Link each forecast to its feature snapshot, evidence artifacts, and model version.
- Generate daily or source-triggered forecasts with probabilities and caveats.
- Backtest at least 100 resolved binary outcomes or explicitly document the smaller sample limitation.
- Report Brier score, log loss, and calibration against base-rate and market benchmarks.
- Display the evidence and forecast history required for a reviewer to reproduce a result.

## 19. Key Decisions to Make Next

1. **Initial domain:** Choose one domain with frequent, clearly resolvable outcomes and reliable data.
2. **Forecast target:** Decide whether the first product forecasts market-resolution outcomes, independent real-world outcomes, or both.
3. **Resolution authority:** Predefine the source hierarchy for every question class.
4. **Publication mode:** Internal research dashboard first, or public forecasts after an audit period.
5. **Data budget:** Confirm paid-data constraints and whether all v0 inputs must be free/open.
6. **Model policy:** Establish when a model may use market prices as an input versus when it must forecast independently.

## 20. Initial Backlog

- [ ] Create PostgreSQL schema and migrations.
- [ ] Define JSON Schema/Pydantic models for question contracts and forecasts.
- [ ] Implement one prediction-market collector with historical snapshot retention.
- [ ] Implement one macro/official-data collector.
- [ ] Implement one RSS/news ingestion collector with source allowlist.
- [ ] Add immutable raw-object storage and provenance hashes.
- [ ] Build question-creation and resolution-review workflow.
- [ ] Build base-rate and market-price baselines.
- [ ] Implement time-aware backtesting and calibration report.
- [ ] Add evidence timeline UI.
- [ ] Draft security, source-licensing, and high-impact-use policies.

## Appendix A: Example Forecast Record

```yaml
forecast_id: f_2026_000124
question_id: q_2026_000001
question_version: 1
as_of: "2026-10-08T12:00:00Z"
horizon: "30d"
model:
  name: "structured_text_ensemble"
  version: "0.3.1"
probabilities:
  yes: 0.62
  no: 0.38
baselines:
  base_rate_yes: 0.43
  market_yes: 0.58
evidence:
  artifact_ids: ["a_101", "a_210", "a_345"]
  claim_ids: ["c_900", "c_901"]
features:
  snapshot_id: "fs_789"
  version: "2026-10-08.1"
caveats:
  - "Market liquidity is below configured threshold."
  - "Two high-tier sources disagree on a leading indicator."
published_at: "2026-10-08T12:05:00Z"
```

## Appendix B: Definitions

- **Calibration:** Whether events assigned a probability of, for example, 70% occur about 70% of the time over many comparable forecasts.
- **Point-in-time correctness:** Ensuring a historical forecast uses only data actually available at that moment.
- **Resolution:** The process of assigning an outcome using predeclared rules and evidence.
- **Provenance:** A record of where data came from, when it was collected, and how it was transformed.
- **Forecast horizon:** The elapsed time between a forecast timestamp and the question’s deadline or predicted event window.
