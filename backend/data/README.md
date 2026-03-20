# Data Directory

Place the Chain IQ provided data files here. The app loads them automatically on startup.

```
data/
├── suppliers.csv          ← Supplier master data
├── pricing.csv            ← Volume pricing tiers
├── policies.json          ← Procurement rules
├── historical_awards.csv  ← Past decisions
└── requests.json          ← Demo/test requests
```

If files are missing, the app falls back to the seed mock data (15 suppliers, 10 rules).

Run manually: `python -m db.loaders`
