import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";

// Every file tool is confined to ./workspace so the agent can't touch the rest of your disk.
export const WORKSPACE = path.resolve("workspace");

function resolveInWorkspace(relativePath: string): string {
  const target = path.resolve(WORKSPACE, relativePath);
  const relative = path.relative(WORKSPACE, target);
  if (relative === ".." || relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) {
    throw new Error(`path escapes the workspace: ${relativePath}`);
  }
  return target;
}

// One definition per tool, shared by both agent styles: manual-loop.ts turns the schema
// into JSON Schema by hand, tool-runner.ts hands it to betaZodTool.
export const tools = [
  {
    name: "list_files",
    description: "List the files in the workspace, recursively. Paths are relative to the workspace root.",
    schema: z.object({}),
    run: async () => {
      const entries = await fs.readdir(WORKSPACE, { recursive: true, withFileTypes: true });
      const files = entries
        .filter((e) => e.isFile())
        .map((e) => path.relative(WORKSPACE, path.join(e.parentPath, e.name)));
      return files.length ? files.join("\n") : "(workspace is empty)";
    },
  },
  {
    name: "read_file",
    description: "Read a text file from the workspace.",
    schema: z.object({ path: z.string().describe("Path relative to the workspace root") }),
    run: async ({ path: p }: { path: string }) => fs.readFile(resolveInWorkspace(p), "utf8"),
  },
  {
    name: "write_file",
    description: "Create or overwrite a text file in the workspace. Parent folders are created as needed.",
    schema: z.object({
      path: z.string().describe("Path relative to the workspace root"),
      contents: z.string(),
    }),
    run: async ({ path: p, contents }: { path: string; contents: string }) => {
      const target = resolveInWorkspace(p);
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, contents);
      return `wrote ${contents.length} characters to ${p}`;
    },
  },
  {
    name: "calculate",
    description: "Evaluate an arithmetic expression with + - * / % ** and parentheses.",
    schema: z.object({ expression: z.string() }),
    run: async ({ expression }: { expression: string }) => {
      if (!/^[\d\s+\-*/%.()]+$/.test(expression)) throw new Error("only numbers and + - * / % ** ( ) are allowed");
      return String(Function(`"use strict"; return (${expression});`)());
    },
  },
];

export const SYSTEM_PROMPT =
  "You are an agent in a sandbox. You can list, read and write files in a workspace folder and do arithmetic. " +
  "Use the tools when they help, then give a short final answer.";
