# alertagent

Agentic alerting for alertmanager events. Receives Alertmanager events, sends a message for each alert to the Discord channel,
then sends each alert to an LLM for deeper triage and root cause analysis, which it then posts into a thread under each alert. 

I wrote this to save myself a bit of time when sitting down in the morning and seeing what broke overnight - the agent has enough access
to the cluster to give at least one plausible explanation for what's going on, which is convenient. Note that it does not have write access
anywhere, by design. I don't want robots making things worse overnight by mistake, rare as that may be. 

## Prerequisites

You should have a Discord bot configured and in your server prior to getting started here. Getting that set up is out of scope for this README, though.

## Configuration

Configured entirely through environment variables. See `mise.toml` for another reference of the full set.

| Env var | Required | Notes |
| --- | --- | --- |
| `ALERTAGENT_DISCORD_BOT_TOKEN` | yes | the "Token" in the Bot section of your discord bot in https://discord.com/developers/applications. |
| `ALERTAGENT_DISCORD_CHANNEL_ID` | yes | Right click the channel, "Copy Channel ID" at the bottom |
| `ALERTAGENT_OPENAI_BASE_URL` | default `https://api.openai.com/v1` | any local OpenAI-compatible endpoint works, or leave at the default for real OpenAI |
| `ALERTAGENT_MODEL_NAME` | default `gpt-5-mini` |  |
| `ALERTAGENT_OPENAI_API_KEY` | yes | for local endpoints that need no key, set to any non-blank string |

## Development

Requires [mise](https://mise.jdx.dev) to be installed and present in `PATH`. Then, to run local dev server:

```
# Starts local dev server on port 8000
mise dev
```

# License

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with this program. If not, see https://www.gnu.org/licenses/.