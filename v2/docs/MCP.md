# Using Drishti from Claude (MCP)

`drishti mcp` is a stdio MCP server over the local database. It reads what the jobs have stored; it never fetches.

| Tool | What it answers |
|---|---|
| `data_health` | What is fresh, what failed, canaries. Ask this first when freshness matters. |
| `resolve_security` | ISIN / NSE symbol / BSE symbol / BSE scrip code -> the security. Several matches are returned as `ambiguous`. |
| `insider_trades` | SEBI insider disclosures, last N days, optional company and direction, with buy/sell totals. |

Every reply is `{data, as_of, sources, error}`. A stale answer carries `error.kind = STALE` together with the data.

## Register in Claude Desktop (Windows)

Add this under `mcpServers` in `%APPDATA%\Claude\claude_desktop_config.json`, then restart Claude Desktop
(adjust the path if the project lives elsewhere):

```json
{
  "mcpServers": {
    "drishti": {
      "command": "uv",
      "args": ["run", "--project", "C:\\Users\\abhij\\Documents\\Drishti\\v2", "drishti", "mcp"]
    }
  }
}
```

Check by asking Claude "run drishti data_health". Fill the database first with `uv run drishti run <job>` (see `drishti jobs`).
This registration edits a file outside the project, so it is done by you, not by the build.

## Adding tools

Write a plain function of a `Store` in `drishti/mcp_server.py` that returns `Result.to_dict()`, test it without the SDK,
then register it in `build_server`. Tools stay profile-neutral; anything that needs a portfolio goes through the
profile layer, which only decisions and tools may read.
