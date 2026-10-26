---
name: angeldot-toolkit
description: Unified index and router for the 10 @angeldot_ Claude Code & Multi-Agent repositories (wshobson/agents, VoltAgent subagents, rohitg00 toolkit, anthropics/skills, netresearch marketplace, disler & karanb192 hooks, claude-swarm, am-will swarms, obsidian-second-brain).
version: 1.0.0
author: Antigravity
license: MIT
metadata:
  hermes:
    tags: [toolkit, subagents, swarms, hooks, second-brain, angeldot]
---

# Angeldot 10-Repo Multi-Agent Ecosystem Toolkit

This skill provides direct local paths and invocation instructions for all 10 repositories from `@angeldot_` (`https://x.com/angeldot_/status/2114374287549157402`), installed across **Antigravity (`~/.gemini/config/`)**, **Claude Code (`~/.claude/`)**, **OpenAI Codex (`~/.codex/`)**, **Shared Agent Hub (`~/.agents/`)**, and **Hermes (`~/.hermes/`)**.

## 1. `wshobson/agents` — Multi-Harness Plugin & Agent Catalog (73+ plugins, 185 skills, 202 agents)
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\wshobson-agents`
- **Antigravity Native Plugins (`.antigravity/plugins/`)**: `C:\Users\ardhy\.agents\angeldot-repos\wshobson-agents\.antigravity\plugins`
  - Link any plugin into Antigravity on demand: `agy plugin install C:\Users\ardhy\.agents\angeldot-repos\wshobson-agents\.antigravity\plugins\<plugin-name>`
- **Codex Native Plugins (`.codex/`)**: `C:\Users\ardhy\.agents\angeldot-repos\wshobson-agents\.codex`
- **Installed Skills**: `C:\Users\ardhy\.claude\skills`, `C:\Users\ardhy\.codex\skills`, `C:\Users\ardhy\.agents\skills`
- **Installed Subagents**: `C:\Users\ardhy\.agents\subagents\wshobson` & `C:\Users\ardhy\.claude\agents`

## 2. `VoltAgent/awesome-claude-code-subagents` — 163 Specialized Subagents
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\voltagent-subagents`
- **Installed Subagents**: `C:\Users\ardhy\.claude\agents` and `C:\Users\ardhy\.agents\subagents\voltagent`
- **Categories**: `01-core-development`, `02-language-specialists`, `03-infrastructure`, `04-quality-security`, `05-data-ai`, `06-developer-experience`, `07-specialized-domains`, `08-business-product`, `09-meta-orchestration`, `10-research-analysis`.

## 3. `rohitg00/awesome-claude-code-toolkit` — 136 Agents, 40 Skills, 42 Commands, 21 Hooks, 16 MCP Configs
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\rohitg00-toolkit`
- **Installed Skills**: `C:\Users\ardhy\.claude\skills`, `C:\Users\ardhy\.codex\skills`, `C:\Users\ardhy\.agents\skills`
- **Installed Subagents**: `C:\Users\ardhy\.agents\subagents\rohitg00` & `C:\Users\ardhy\.claude\agents`
- **Installed Commands**: `C:\Users\ardhy\.claude\commands`
- **Installed Hooks**: `C:\Users\ardhy\.claude\hooks\rohitg00-toolkit`
- **Installed MCP Config Templates**: `C:\Users\ardhy\.agents\mcp-configs\rohitg00-mcp-configs`

## 4. `anthropics/skills` — Official Anthropic Agent Skills (19 Skills)
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\anthropics-skills\skills`
- **Installed Across All Harnesses**: `claude-api`, `mcp-builder`, `webapp-testing`, `web-artifacts-builder`, `docx`, `pdf`, `pptx`, `xlsx`, `doc-coauthoring`, `frontend-design`, `canvas-design`, `algorithmic-art`, `brand-guidelines`, `internal-comms`, `skill-creator`, `slack-gif-creator`, `theme-factory`, `academy-guide`, `discernment-nudge`.

## 5. `netresearch/claude-code-marketplace` — 40 Curated Stack & Security Skills
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\netresearch-marketplace`
- **Installed Catalogs**:
  - Claude Code: `C:\Users\ardhy\.claude\plugins\netresearch-marketplace\marketplace.json`
  - Codex / Shared Agents: `C:\Users\ardhy\.agents\plugins\netresearch-marketplace.json`

## 6. `disler/claude-code-hooks-mastery` — Lifecycle Hook Mastery & Team Agents
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\disler-hooks-mastery`
- **Installed Hooks**: `C:\Users\ardhy\.claude\hooks\disler-hooks-mastery` (`pre_tool_use.py`, `post_tool_use.py`, `notification.py`, `session_start.py`, `session_end.py`, `subagent_start.py`, `subagent_stop.py`, `user_prompt_submit.py`, `pre_compact.py`, `stop.py` + LLM/TTS utils)
- **Installed Team Agents**: `C:\Users\ardhy\.agents\subagents\disler`

## 7. `karanb192/claude-code-hooks` — Security, Spend Control, & Productivity Hooks (22 Plugins + 8 Skills)
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\karanb192-hooks`
- **Installed Hook Plugins**: `C:\Users\ardhy\.claude\hooks\karanb192-hooks` (`block-dangerous-commands`, `git-safety`, `config-guard`, `cache-tax`, `context-hogs`, `dead-end-registry`, `dead-rules-audit`, `bounty-board`, `nerf-receipts`, `pr-provenance-stamp`, `standup-autopilot`, `auto-stage`, `format-code`)
- **Installed Hook Skills**: `hook-bounty-board-board`, `hook-cache-tax-status`, `hook-context-hogs-leaderboard`, `hook-dead-end-registry-dead-ends`, `hook-dead-rules-audit-scorecard`, `hook-nerf-receipts-receipts`, `hook-pr-provenance-stamp-provenance`, `hook-standup-autopilot-standup`.

## 8. `affaan-m/claude-swarm` — Multi-Agent Swarm Orchestrator CLI
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\affaan-claude-swarm`
- **CLI Usage**: `claude-swarm --demo`, `claude-swarm --dry-run "<task>"`, `claude-swarm --budget 5.0 --max-agents 4 "<task>"`
- **Skill**: `claude-swarm` (`SKILL.md` installed in Antigravity, Claude Code, Codex, and Hermes).

## 9. `am-will/swarms` — 7 On-Demand Multi-Agent Coordination Skills
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\am-will-swarms`
- **Installed Skills**: `co-design`, `parallel-task`, `parallel-task-spark`, `parallel-task-tmux`, `super-swarm`, `super-swarm-spark`, `swarm-planner`.

## 10. `eugeniughelbur/obsidian-second-brain` — Shared AI-First Markdown Second Brain
- **Source Repo**: `C:\Users\ardhy\.agents\angeldot-repos\obsidian-second-brain`
- **Live Shared Vault**: `C:\Users\ardhy\.agents\second-brain-vault` (with `_CLAUDE.md`, `index.md`, `log.md`, `CRITICAL_FACTS.md`, `SOUL.md`, `wiki/`, `raw/`)
- **Skill**: `obsidian-second-brain` installed in Antigravity, Claude Code, Codex, and Hermes (`~/.hermes/skills/obsidian-second-brain`).
