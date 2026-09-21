import asyncio
import os
import json
from anthropic import AsyncAnthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# We mock DeepSeek integration via Anthropic SDK as requested
# ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic" needs to be set in env or passed
anthropic = AsyncAnthropic(
    base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"),
    api_key=os.environ.get("ANTHROPIC_API_KEY", "dummy_key")
)

# Setup MCP client for our Playwright server
server_params = StdioServerParameters(
    command="python",
    args=["/app/server.py"],
    env=None
)

async def run_repl():
    print("Agent REPL Started. Type your task (or 'exit' to quit).")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Fetch tools
            tools_resp = await session.list_tools()
            tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema
                }
                for t in tools_resp.tools
            ]

            while True:
                try:
                    user_input = input("Task > ")
                except EOFError:
                    break

                if user_input.lower() in ['exit', 'quit']:
                    break

                messages = [
                    {"role": "user", "content": user_input}
                ]

                while True:
                    # Cognitive Memory Eviction
                    for msg in messages[:-1]:
                        if isinstance(msg["content"], str) and "New ARIA snapshot:" in msg["content"]:
                            msg["content"] = msg["content"].split("New ARIA snapshot:")[0] + "\n[ARIA Snapshot Evicted from Memory]"

                    print("\n[Thinking...]")

                    try:
                        response = await anthropic.messages.create(
                            model="deepseek-chat",
                            max_tokens=4000,
                            messages=messages,
                            tools=tools,
                            tool_choice={"type": "auto"}
                        )
                    except Exception as e:
                        print(f"Error calling LLM: {e}")
                        break

                    if hasattr(response, 'reasoning_content') and response.reasoning_content:
                        print(f"\n[Reasoning]\n{response.reasoning_content}\n")

                    if response.stop_reason == "tool_use":
                        tool_use = next(b for b in response.content if b.type == "tool_use")
                        tool_name = tool_use.name
                        tool_args = tool_use.input

                        print(f"\n[Action] {tool_name}({tool_args})")

                        messages.append({"role": "assistant", "content": response.content})

                        try:
                            result = await session.call_tool(tool_name, tool_args)
                            tool_result = result.content[0].text
                            print(f"\n[Result]\n{tool_result[:500]}...\n")

                            messages.append({
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": tool_use.id,
                                        "content": tool_result
                                    }
                                ]
                            })
                        except Exception as e:
                            print(f"Tool execution failed: {e}")
                            messages.append({
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": tool_use.id,
                                        "content": f"Error: {e}",
                                        "is_error": True
                                    }
                                ]
                            })
                    else:
                        final_text = next((b.text for b in response.content if b.type == "text"), "Task complete.")
                        print(f"\n[Final Response]\n{final_text}\n")
                        break

if __name__ == "__main__":
    asyncio.run(run_repl())
