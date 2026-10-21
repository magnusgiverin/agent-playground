// The agent loop written out by hand: call the model, run any tools it asks for,
// send the results back, repeat until it stops asking.
import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { SYSTEM_PROMPT, tools } from "./tools.js";

const MODEL = "claude-opus-5-5";
const MAX_TURNS = 20;

const client = new Anthropic();
const prompt = process.argv.slice(2).join(" ") || "List the workspace, then write hello.md with a haiku about agents.";

const toolDefinitions: Anthropic.Tool[] = tools.map((t) => ({
  name: t.name,
  description: t.description,
  input_schema: z.toJSONSchema(t.schema) as Anthropic.Tool.InputSchema,
}));

async function runTool(block: Anthropic.ToolUseBlock): Promise<Anthropic.ToolResultBlockParam> {
  const tool = tools.find((t) => t.name === block.name);
  try {
    if (!tool) throw new Error(`unknown tool: ${block.name}`);
    const input = tool.schema.parse(block.input);
    const content = await tool.run(input as never);
    return { type: "tool_result", tool_use_id: block.id, content };
  } catch (error) {
    // Errors go back to the model so it can recover, instead of crashing the loop.
    return { type: "tool_result", tool_use_id: block.id, content: String(error), is_error: true };
  }
}

const messages: Anthropic.MessageParam[] = [{ role: "user", content: prompt }];
console.log(`> ${prompt}\n`);

for (let turn = 1; turn <= MAX_TURNS; turn++) {
  const response = await client.messages.create({
    model: MODEL,
    max_tokens: 16000,
    system: SYSTEM_PROMPT,
    tools: toolDefinitions,
    messages,
  });

  // Append the whole content, not just the text: tool_use and thinking blocks must be sent back.
  messages.push({ role: "assistant", content: response.content });

  for (const block of response.content) {
    if (block.type === "text") console.log(block.text);
    if (block.type === "tool_use") console.log(`  [tool] ${block.name} ${JSON.stringify(block.input)}`);
  }

  if (response.stop_reason === "pause_turn") continue;
  if (response.stop_reason !== "tool_use") {
    console.log(`\n(stop_reason: ${response.stop_reason}, turns: ${turn}, output tokens: ${response.usage.output_tokens})`);
    process.exit(0);
  }

  // Run all requested tools in parallel and return every result in a single user message.
  const toolUses = response.content.filter((b): b is Anthropic.ToolUseBlock => b.type === "tool_use");
  messages.push({ role: "user", content: await Promise.all(toolUses.map(runTool)) });
}

console.log(`\nGave up after ${MAX_TURNS} turns.`);
