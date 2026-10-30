# CLAUDE.md

Guidance for Claude Code when working in this repository.

## About this project

- This is the GitHub repo `UMW-GBUS562`, coursework for **GBUS 562: AI Language Models** at the University of Mary Washington.
- The goal of the course work here is building software tools that use large language models (mainly Claude).
- Work is organized into weekly folders (`week-01/`, `week-02/`, ...). Each new week gets its own folder and a link in the "Weekly Material" list in `README.md`.
- `week-01/` holds last week's work: a Connect Four browser game (`connect-four.html`) with a two-player mode.

## Anthropic API key

- The Anthropic API key is stored as a **GitHub Codespaces secret** and is available in the codespace as the environment variable **`API_KEY`**.
- Use this key whenever we build something that connects to Claude. Read it from the environment at runtime, for example:
  - Python: `anthropic.Anthropic(api_key=os.environ["API_KEY"])`
  - JavaScript: `new Anthropic({ apiKey: process.env.API_KEY })`
- Note: the Anthropic SDKs look for `ANTHROPIC_API_KEY` by default, so always pass `API_KEY` explicitly (or map it: `export ANTHROPIC_API_KEY="$API_KEY"`).

## Security rules for the API key (always follow)

- **NEVER publish the API key to GitHub.** Never commit it, push it, or put it in a PR, issue, or comment.
- Never hard-code the key in any file (source code, HTML, notebooks, config, docs, or examples).
- Never print, echo, or log the key's value in terminal output, and never include it in artifacts or shared pages.
- Do not put the key in client-side code (e.g., browser HTML/JS) where anyone could read it. Browser apps that call Claude need a small server-side piece that holds the key.
- If a `.env` file is ever used, make sure it is listed in `.gitignore` before creating it.
- Before every commit, check the staged changes (`git diff --cached`) to confirm no secrets are included.
