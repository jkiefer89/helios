# Starter Research Models

These CSVs are analysis-only starter templates for Helios. They are not
investment advice, a recommendation, a managed account model, or a guarantee of
returns. Use Helios' provenance gates, Strategy Lab, Portfolio Clinic, and
Advisor Report before treating any model as research evidence.

The templates emphasize liquid public ETFs and widely followed large-cap names
across 2026 forward-looking themes:

- AI infrastructure and hyperscale capex beneficiaries.
- Power, grid, and industrial infrastructure for data-center demand.
- Defense and cybersecurity for geopolitical fragmentation.
- Healthcare, longevity, and medical technology.
- Quality-growth plus low-volatility and high-quality bonds.
- Real assets and inflation/geopolitical resilience.
- Cash and defensive reserve exposure.

The React Models workspace also exposes these as governed library templates
with mandates, benchmarks, rebalance rules, risk limits, and provenance fields.
Use the library import action, or import the CSVs manually.

To import manually:

```bash
curl -F file=@examples/models/ai-infrastructure-compounders.csv \
  -F name="AI Infrastructure Compounders" \
  -F mandate=pure_growth \
  -F context="Starter research model; analysis only." \
  http://127.0.0.1:5057/api/model/upload
```

Or use the library API:

```bash
curl -X POST http://127.0.0.1:5057/api/model-library/import \
  -H "Content-Type: application/json" \
  -d '{"slug":"ai-infrastructure"}'
```

Before using a template, fetch live histories for every holding or run Helios
with the built-in starter-model live universe:

```bash
HELIOS_AUTO_LIVE_SYMBOLS=starter_models ./run.sh
```

Sample or missing histories must remain blocked or clearly labeled by
provenance gates.
