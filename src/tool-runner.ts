// The same agent, but the SDK's tool runner owns the loop. You only supply the tools.
import Anthropic from "@anthropic-ai/sdk";
import { betaZodTool } from "@anthropic-ai/sdk/helpers/beta/zod";
import { SYSTEM_PROMPT, tools } from "./tools.js";

const MODEL = "claude-opus-5-5";

const client = new Anthropic();
const prompt = process.argv.slice(2).join(" ") || "What is 17 * 23 + 4? Save the answer to answer.txt.";

const runnableTools = tools.map((t) =>
  betaZodTool({ name: t.name, description: t.description, inputSchema: t.schema, run: t.run as never }),
);

console.log(`> ${prompt}\n`);

const runner = client.beta.messages.toolRunner({
  model: MODEL,
  max_tokens: 16000,
  max_iterations: 20,
  system: SYSTEM_PROMPT,
  tools: runnableTools,
  messages: [{ role: "user", content: prompt }],
});

// Iterating yields each assistant message as it arrives; the runner executes tools in between.
for await (const message of runner) {
  for (const block of message.content) {
    if (block.type === "text") console.log(block.text);
    if (block.type === "tool_use") console.log(`  [tool] ${block.name} ${JSON.stringify(block.input)}`);
  }
}

const final = await runner.done();
console.log(`\n(stop_reason: ${final.stop_reason})`);
