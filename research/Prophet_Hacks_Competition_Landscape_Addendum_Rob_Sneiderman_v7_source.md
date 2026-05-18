# Prophet Hacks Competition Landscape Addendum v7
Prepared by Rob Sneiderman

**Purpose.** This addendum clarifies who this event is probably targeting, what competitor methods to expect, and how that should shape Rob Sneiderman's preparation. The core read: this is not a generic app hackathon; it is a graduate-level AI forecasting / statistics / markets / agent-systems event.

# 1. Target audience and student/course pipeline

The event is built for a research-builder crowd. The official workshop frames forecasting as a rich ML tradition spanning time-series analysis, online learning, data-driven decisions, and quantitative finance, then asks whether general-purpose AI systems can reliably anticipate diverse real-world events. It also explicitly aims to bring together machine learning, statistics, economics, finance, and related communities.

Pipeline / background | What they bring | What they may miss | Our response
CS / ML / AI grad students | LLM agents, RAG, model APIs, benchmark design | calibration, market mechanics, risk discipline | beat them with monitoring, market prior, and statistical calibration
Statistics / math / econometrics | proper scoring, uncertainty, shrinkage, SAE, causal thinking | agent orchestration, API packaging, live execution | turn Rob's statistics background into a system advantage
Quant finance / economics | market priors, EV, spreads, liquidity, microstructure | LLM retrieval, source scoring, benchmark infra | use Kalshi lessons plus frontier reasoning
Forecasting / superforecasting | base rates, updates, scenario thinking, calibration habits | automation at scale, CI, model routing | encode their habits as prompts, logs, and calibration checks
RL / online learning | expert weighting, regret, adaptive policies | sparse delayed rewards, overengineering risk | use RL-lite only: routing and expert weights
Backend / MLOps builders | reliable loops, logs, CI, deployment | forecast probability quality | match their robustness while adding statistical edge

# 2. What kind of competition this is

This is best treated as a live benchmark + agent engineering + probabilistic forecasting competition. The hackathon has forecasting and trading tracks evaluated through Prophet Arena, plus a Best PR to AI Prophet track. That means a serious submission can win in multiple ways: leaderboard performance, robust system design, and useful open-source contribution.

Competitor archetype | Likely build | Likely weakness | How we exploit it
LLM-agent teams | multi-agent RAG forecaster, debate prompts, web search | source noise, weak calibration, hard-to-debug traces | simple monitored pipeline + source scoring + calibration
Quant/market teams | market baseline, EV thresholds, spread-aware trades | less strong on LLM evidence synthesis | frontier model only when it can beat market prior
Research teams | offline benchmark, plots, ablations, paper-style report | may not survive live API/tick loop | keep engineering path green first
Infra teams | CI, packaging, logs, dashboards | probability quality may be shallow | combine MLOps discipline with scoring rules/SAE
Forecasting community teams | base rates, scenario analysis, human-style updates | not fully automated or source-gated | automate their strongest habits

**Default competitive stance.** Do not try to be the flashiest agent. Try to be the most calibrated, monitored, source-aware, market-aware system. A boring-looking system that always knows why it acted can beat a dramatic system that cannot diagnose its errors.

# 3. Course map -> repo modules

The workshop topics can be translated directly into modules. This keeps the work concrete and prevents paper-reading from becoming procrastination.

Workshop/course area | Module to build | Metric/check
Architectures: agentic systems, LLM-as-a-Prophet | forecasting/forecaster.py, providers.py, prompts.py | parse failure rate, forecast coverage
Evaluation: metrics and benchmark design | evaluation/brier.py, ece.py, compare_variants.py | Brier, ECE, skill vs market, CI
Reasoning: probabilistic, calibration, causal/temporal | calibration.py, sae_shrinkage.py, horizon features | reliability diagrams by domain/horizon
Retrieval: search, credibility, RAG | source_gate.py, source_scoring.py | source quality lift, retrieved_at checks
Foundations: scoring rules, online learning, decision theory | expert_pool.py, risk.py, edge.py | expert weights, EV/utility, regret-style summaries
Markets & society: prediction markets | longshot_guard.py, price_bucket_diagnostics.py | price-bin Brier/return, longshot failures

# 4. What judges/organizers are likely to value

Because top teams can be invited to present at the ICML workshop, the project should read as both a working agent and a research artifact. The workshop call values architectures, evaluation, probabilistic reasoning, retrieval, foundations, prediction markets, and societal impact. Our report and repo should speak that language explicitly.

They may value | What to show in our artifact
Probabilistic forecasting, not binary guessing | Brier/ECE/market-return tables, reliability diagrams
Comparison to market baseline | market_only variant and skill-vs-market report
Source-quality awareness | source records with credibility/staleness and lift analysis
Modular system design | separate retrieval, forecast, calibration, risk, monitoring modules
Research insight | SAE-style borrowed strength, Kalshi longshot guard, hard-case mining
Reproducibility | GitHub Actions, fixtures, raw hashes, clean package test
Open-source contribution | possible PR to AI Prophet: docs, validation, example bot, monitoring utilities

**Side-channel opportunity.** The Best PR to AI Prophet track is not a distraction if scoped correctly. A good PR could be an example monitoring bot, schema validator, documentation fix, or trace-summary utility. It can support the main project and create a separate route to recognition.

# 5. Additional notes for prep

Note | Implication
Expect mixed skill levels, but serious top teams. | The median team may build a generic RAG agent. The top teams will understand market baselines, calibration, model routing, and benchmark constraints.
The event is research-adjacent. | Because it connects to an ICML workshop, a clear artifact matters. Keep the PDF, run summaries, and ablations clean enough to share.
Our comparative edge is unusual. | Rob has statistics/SAE/data engineering instincts plus AI-builder execution. Most teams will not combine these naturally.
Avoid false sophistication. | A multi-agent debate system is less impressive than a monitored forecast stack that beats market-only on holdout and explains every trade.
Prepare a 4-page workshop-style summary early. | Even if not submitted as a paper, it forces clarity: thesis, method, experiments, results, limitations.
Track every failure as evidence. | A failed forecast with trace, source records, and calibration bucket is useful. A failed forecast with only a rationale is mostly noise.

**Concrete next repo additions.** reports/competition_landscape.md, reports/presentation_outline.md, tools/price_bucket_diagnostics.py, tools/source_lift_analysis.py, .github/workflows/offline-eval.yml, and a possible ai-prophet PR idea list.

# 6. Grounding references for this addendum

Official workshop/hackathon pages: the workshop is titled Forecasting as a New Frontier of Intelligence at ICML 2026, lists target communities across ML, statistics, economics, and finance, and lists topics that map directly to our modules. Prophet Hacks is a 30-hour Chicago/remote sprint with forecasting and trading tracks evaluated through Prophet Arena plus a Best PR to AI Prophet award.

Project papers/files: the Prophet Arena paper defines the event/context/prediction pipeline and metrics; the Kalshi paper motivates market microstructure and longshot diagnostics; Rob's baseline strategy, budget policy, operating pack, research spine, and submission checklist define the engineering constraints and risk posture.

Action: append this addendum to the full playbook and use it to orient coding agents before they touch repo architecture.
