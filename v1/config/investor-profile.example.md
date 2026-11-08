# Investor profile (OPTIONAL, private)

Copy this to `config/investor-profile.md` (the installer can do it for you) and fill in only
what you want. Commands use it **only if that file exists** — otherwise every command behaves
generically. The real file is gitignored and never committed. Delete it any time to go back to
generic behaviour.

This shapes *how recommendations are framed for you* — it is not a net-worth tracker and holds
no goals or balances beyond what you choose to write.

## Risk & horizon
- risk_appetite:        <!-- conservative | balanced | aggressive -->
- time_horizon:         <!-- e.g. 3-5 years -->
- max_position_size:    <!-- e.g. 5% of equity, or a rupee cap per name -->

## Capital (only if you want /entry to size against it)
- deployable_capital:   <!-- amount you're willing to deploy; used only for sizing math -->

## Preferences
- prefer_sectors:       <!-- e.g. capex, financials -->
- avoid_sectors:        <!-- e.g. tobacco, weak-governance names -->
- notes:                <!-- anything else that should shape how names are judged for you -->
