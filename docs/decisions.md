# MarketHub — Dependency / Architecture Decisions

## 2026-09-22 — X/Twitter engine pinned to twitter-cli==0.8.5

- Repository: https://github.com/public-clis/twitter-cli (Apache-2.0).
- Pin: PyPI `twitter-cli==0.8.5`, commit
  `7c634e0d396b1e7af9f63315b414925fe4f29ae7` (2026-05-07T15:53:22Z).
- Install: `pipx install twitter-cli==0.8.5`; resolved exe
  `%USERPROFILE%\.local\bin\twitter-cli.exe`, stored in `x_config.cli_path`.
- Rationale: read-only `--json` contract used by all 7 MCP tools + poll loop;
  previously live-validated in this project.
- Change gate: startup/provisioning runs `twitter-cli --version` → `0.8.5`;
  drift surfaces `x_unavailable` (polling pauses, cache stays readable).
  Never auto-upgrade. An upgrade is a deliberate decision recorded here
  after re-validation of every read path + poll ordering + acceptance
  criteria in `docs/x-twitter-feature-plan.md` §11.
