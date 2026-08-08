# Candidate task rules

- Work only inside this workspace.
- Use only the provided visible episodes and their TRAIN/VALIDATION splits.
- Do not use network access, web search, browsers, apps, connectors, parent
  directories, repository history, or external market data.
- Do not inspect, locate, infer, download, decrypt, or reconstruct holdout data,
  real symbols, or calendar dates.
- Edit only `strategy/candidate_strategy.py`, `strategy/design.md`,
  `strategy/self_assessment.md`, and `strategy/experiment_log.csv`.
- Keep the scaffold, tests, configuration, manifests, and visible data unchanged.
- Run `python -m pytest -q` and `python run_visible.py` before finishing.
