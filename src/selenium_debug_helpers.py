import os
import json
from typing import Any, Dict, List

# Save a fully rendered page snapshot + extras to data/debug/<name>_rendered.html, <name>.png, etc.
def save_full_page(driver, name: str) -> Dict[str, Any]:
    """
    Save a rendered snapshot of the current page the Selenium driver sees.

    Files produced (in repo/data/debug):
      - {name}_rendered.html    : document.documentElement.outerHTML (preferred)
      - {name}.png              : screenshot (PNG)
      - {name}_shadows.json     : list of discovered shadow hosts and shadow innerHTML (best-effort)
      - {name}_iframes.json     : list of iframe srcs (content not captured cross-origin)
      - {name}_meta.json        : small JSON with basic info (current_url, title, page_source_len)

    Returns a dict with the same info.
    """
    os.makedirs("data/debug", exist_ok=True)
    base = os.path.join("data", "debug", name)
    out: Dict[str, Any] = {"name": name, "saved": []}

    # 1) try to get the live DOM from the browser (preferred)
    try:
        html = driver.execute_script("return document.documentElement.outerHTML;")
    except Exception:
        # fallback to page_source if script access is restricted
        try:
            html = driver.page_source or ""
        except Exception:
            html = ""

    try:
        path_html = f"{base}_rendered.html"
        with open(path_html, "w", encoding="utf-8") as fh:
            fh.write(html)
        out["saved"].append(path_html)
    except Exception as e:
        out["html_error"] = str(e)

    # 2) Save screenshot (best-effort)
    try:
        path_png = f"{base}.png"
        driver.save_screenshot(path_png)
        out["saved"].append(path_png)
    except Exception as e:
        out["screenshot_error"] = str(e)

    # 3) Capture shadow host innerHTMLs (best-effort)
    try:
        shadows = driver.execute_script("""
            const out = [];
            function walk(node, path) {
                if (node.shadowRoot) {
                    out.push({ path: path, html: node.shadowRoot.innerHTML.substring(0, 20000) });
                }
                for (let i=0;i<node.children.length;i++){
                    const c = node.children[i];
                    const seg = c.tagName ? c.tagName.toLowerCase() : 'node';
                    const id = c.id ? ('#'+c.id) : '';
                    const cls = c.className ? ('.'+c.className.replace(/\\s+/g,'.')) : '';
                    walk(c, path + '>' + seg + id + cls);
                }
            }
            walk(document.documentElement, document.documentElement.tagName.toLowerCase());
            return out;
        """)
        path_sh = f"{base}_shadows.json"
        with open(path_sh, "w", encoding="utf-8") as fh:
            json.dump(shadows, fh, indent=2)
        out["saved"].append(path_sh)
    except Exception as e:
        out["shadows_error"] = str(e)

    # 4) Collect iframe srcs (cannot read cross-origin iframe contents)
    try:
        iframes = driver.execute_script("""
            return Array.from(document.querySelectorAll('iframe')).map((f,i)=>({
                index: i,
                src: f.getAttribute('src'),
                id: f.id || null,
                name: f.name || null
            }));
        """)
        path_if = f"{base}_iframes.json"
        with open(path_if, "w", encoding="utf-8") as fh:
            json.dump(iframes, fh, indent=2)
        out["saved"].append(path_if)
    except Exception as e:
        out["iframes_error"] = str(e)

    # 5) meta info
    try:
        meta = {
            "current_url": driver.current_url if hasattr(driver, "current_url") else None,
            "title": driver.title if hasattr(driver, "title") else None,
            "page_source_len": len(html) if html else 0
        }
        path_meta = f"{base}_meta.json"
        with open(path_meta, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        out["saved"].append(path_meta)
        out["meta"] = meta
    except Exception as e:
        out["meta_error"] = str(e)

    return out