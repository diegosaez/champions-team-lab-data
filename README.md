# Champions Team Lab data

Game data for the Team Lab iPhone app (Pokémon Champions team builder).

A GitHub Action (`.github/workflows/update-data.yml`) runs every Monday. It rebuilds
`data/gamedata.json` from public sources and commits it only when the stats changed.
The app checks `data/version.json` and downloads the new file when it is newer.

Sources: Smogon ladder statistics, Pikalytics, Pokémon Showdown data (Champions mod), Champdex.

Run it by hand: Actions → Update data → Run workflow.
