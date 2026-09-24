import asyncio
from mcp.server.mcpserver import MCPServer
from playwright.async_api import async_playwright
import tempfile
import sys
import yaml
import json

mcp = MCPServer("PlaywrightBrowser")

# Global state
_playwright = None
_browser = None
_page = None
_dead_ends = []
_last_url = None
_last_aria = None

async def init_browser():
    global _playwright, _browser, _page
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch_persistent_context(
        user_data_dir="/tmp/playwright_user_data",
        headless=False,
        args=["--no-sandbox", "--disable-setuid-sandbox"]
    )
    pages = _browser.pages
    if len(pages) > 0:
        _page = pages[0]
    else:
        _page = await _browser.new_page()

async def get_aria_snapshot(page):
    js_script = """
    () => {
        // Clear previous badges
        document.querySelectorAll('.ai-som-badge').forEach(e => e.remove());

        const interactiveElements = Array.from(document.querySelectorAll('a, button, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [tabindex]:not([tabindex="-1"])'));
        let result = [];
        let index = 1;
        for (const el of interactiveElements) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;

            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;

            const name = el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
            const role = el.getAttribute('role') || el.tagName.toLowerCase();
            const id = 'e' + index;
            index++;

            el.setAttribute('data-ai-ref', id);

            // Create a visual badge for the user to see what the agent is targeting
            const badge = document.createElement('div');
            badge.className = 'ai-som-badge';
            badge.textContent = id;
            badge.style.position = 'absolute';
            badge.style.left = (rect.x + window.scrollX) + 'px';
            badge.style.top = (rect.y + window.scrollY) + 'px';
            badge.style.backgroundColor = 'rgba(255, 0, 0, 0.8)';
            badge.style.color = 'white';
            badge.style.fontSize = '12px';
            badge.style.fontWeight = 'bold';
            badge.style.padding = '2px 4px';
            badge.style.borderRadius = '3px';
            badge.style.zIndex = '999999';
            badge.style.pointerEvents = 'none'; // so it doesn't block clicks
            document.body.appendChild(badge);

            result.push({
                ref: id,
                role: role,
                name: name.trim().substring(0, 50).replace(/\n/g, ' '),
                box: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)]
            });
        }
        return result;
    }
    """
    try:
        elements = await page.evaluate(js_script)
        if not elements:
            return "No interactive elements found."
        yaml_str = yaml.dump(elements, default_flow_style=None, sort_keys=False)
        return yaml_str
    except Exception as e:
        return f"Error capturing ARIA snapshot: {str(e)}"

@mcp.tool()
async def execute_browser_action(
    action: str,
    reasoning_content: str,
    url: str = None,
    ref: str = None,
    value: str = None,
    direction: str = None
) -> str:
    """
    Executes a browser action and returns the new ARIA snapshot.
    Supported actions: navigate, click, fill, scroll, extract_text.
    - navigate: requires url
    - click: requires ref
    - fill: requires ref and value
    - scroll: requires direction ("up" or "down")
    - extract_text: extracts text from the current page
    """
    global _page, _dead_ends, _last_url, _last_aria

    action_key = f"{action}:{url}:{ref}:{value}:{direction}"

    if action_key in _dead_ends:
        return f"Error: Action '{action_key}' is in dead-end cache. Try something else."

    try:
        if action == "navigate":
            await _page.goto(url, wait_until="networkidle")
        elif action in ["click", "fill"]:
            element = _page.locator(f"[data-ai-ref='{ref}']")

            # Highlight the element visually before acting
            await element.evaluate("""(el) => {
                el.style.outline = '4px solid yellow';
                el.style.transition = 'outline 0.1s';
            }""")
            await _page.wait_for_timeout(500) # give user a chance to see

            if action == "click":
                await element.click()
                await _page.wait_for_timeout(2000)
            elif action == "fill":
                await element.fill(value)

            # Try to remove the outline if it hasn't navigated away
            try:
                await element.evaluate("""(el) => {
                    el.style.outline = '';
                }""")
            except:
                pass

        elif action == "scroll":
            if direction == "down":
                await _page.evaluate("window.scrollBy(0, window.innerHeight)")
            elif direction == "up":
                await _page.evaluate("window.scrollBy(0, -window.innerHeight)")
            await _page.wait_for_timeout(1000)
        elif action == "extract_text":
            text = await _page.evaluate("document.body.innerText")
            return f"Extracted Text:\n{text[:2000]}...\n\nCurrent state:\n" + await get_aria_snapshot(_page)
        else:
            return f"Error: Unknown action '{action}'"

        current_url = _page.url
        current_aria = await get_aria_snapshot(_page)

        if action in ["navigate", "click", "scroll"]:
            if current_url == _last_url and current_aria == _last_aria:
                _dead_ends.append(action_key)
                return f"Error: Action '{action_key}' resulted in no DOM/URL changes. Added to dead-end cache. Current state:\n{current_aria}"

        _last_url = current_url
        _last_aria = current_aria

        return f"Success. New ARIA snapshot:\n{current_aria}"

    except Exception as e:
        _dead_ends.append(action_key)
        return f"Error executing '{action}': {str(e)}. Added to dead-end cache."

async def main():
    await init_browser()
    await mcp.run_stdio_async()

if __name__ == "__main__":
    asyncio.run(main())
