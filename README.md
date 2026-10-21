# agent-playground

A playground for building Claude agents in TypeScript and poking at how they work.

## Concepts in 60 seconds

- An **agent** is a model in a loop: it gets a task and a set of **tools**, decides which tool to call, sees the result, and repeats until it's done.
- The loop runs against one endpoint, `POST /v1/messages`:
  1. You send `messages` and `tools`.
  2. If `stop_reason` is `tool_use`, the response contains `tool_use` blocks. You run them and send back `tool_result` blocks in a single user message.
  3. You repeat until `stop_reason` is `end_turn`.
- **The model never runs anything itself.** Your code runs every tool, so your code decides what the agent is allowed to touch.

## Setup

```sh
npm install
export ANTHROPIC_API_KEY=sk-ant-...
```

## The agents

Both agents share the tools in [src/tools.ts](src/tools.ts): `list_files`, `read_file`, `write_file` (all confined to [workspace/](workspace/)) and `calculate`.

| Script | File | What it shows |
|---|---|---|
| `npm run manual -- "<task>"` | [src/manual-loop.ts](src/manual-loop.ts) | The loop written by hand: stop reasons, parallel tool calls, returning errors to the model |
| `npm run runner -- "<task>"` | [src/tool-runner.ts](src/tool-runner.ts) | The same agent with the SDK's tool runner (`client.beta.messages.toolRunner`) owning the loop |

Leave out the task to run a default one. Things to try:

- `npm run manual -- "Write a short story across three files in stories/, then summarize them in index.md"`
- `npm run runner -- "Read ../package.json"` to watch the sandbox refuse and the model recover
- `npm run manual -- "What is 2**64 divided by 7? Show your work in math.md"`

## Exercises

- Add a tool that calls a real API with `fetch`, such as weather or GitHub.
- Ask for approval in the terminal before `write_file` runs (human in the loop).
- Stream the output with `client.messages.stream()` and `eager_input_streaming: true`.
- Add a server tool such as `{ type: "web_search_20260209", name: "web_search" }`. It runs on Anthropic's side, so there's no `run` function.
- Track tokens and cost per run from `response.usage`.
- Give the agent a sub-agent: a tool whose `run` starts another agent loop with a narrower prompt.
- Compare with the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk), which ships Claude Code's built-in tools and loop as a library.

Models default to `claude-opus-5-5`. Change `MODEL` at the top of each script to try another one.
