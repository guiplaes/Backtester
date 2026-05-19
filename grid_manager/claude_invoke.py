"""
Invoke Claude CLI in headless mode with a prompt + context.
Returns the structured decision Claude makes.
"""
import json
import subprocess
import time
from pathlib import Path

from config import CLAUDE_CLI, CLAUDE_TIMEOUT_SEC, PROMPTS_DIR


def load_prompt(name: str) -> str:
    """Load prompt template from prompts/ folder."""
    p = PROMPTS_DIR / f"{name}.md"
    if not p.exists():
        raise FileNotFoundError(f"Prompt {p} not found")
    return p.read_text(encoding="utf-8")


def invoke_claude(prompt: str, working_dir: str = None) -> dict:
    """
    Run Claude CLI in headless mode (-p flag).
    Returns dict with 'output' and 'duration_sec'.
    """
    start = time.time()
    cmd = [CLAUDE_CLI, "-p", prompt]
    # CREATE_NO_WINDOW (0x08000000) evita que claude.cmd obri finestra cmd visible
    # Nomes Windows — en altres OS el flag s'ignora
    creationflags = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SEC,
            cwd=working_dir,
            creationflags=creationflags,
        )
        duration = time.time() - start
        return {
            "output": result.stdout,
            "error": result.stderr if result.returncode != 0 else None,
            "duration_sec": duration,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "output": "",
            "error": f"Claude CLI timeout after {CLAUDE_TIMEOUT_SEC}s",
            "duration_sec": CLAUDE_TIMEOUT_SEC,
            "returncode": -1,
        }


def parse_decision(claude_output: str) -> dict:
    """Parse Claude's structured response."""
    lines = claude_output.splitlines()
    out = {}
    for line in lines:
        if line.startswith("DECISION:"):
            out["decision"] = line.split(":", 1)[1].strip()
        elif line.startswith("REASONING:"):
            out["reasoning"] = line.split(":", 1)[1].strip()
        elif line.startswith("NEW_RANGE:"):
            r = line.split(":", 1)[1].strip()
            if r.lower() not in ("null", "none", "-"):
                try:
                    parts = r.strip("[]").split(",")
                    out["new_range"] = (float(parts[0]), float(parts[1]))
                except Exception:
                    out["new_range"] = None
            else:
                out["new_range"] = None
        elif line.startswith("NEW_GRIDS:"):
            g = line.split(":", 1)[1].strip()
            if g.lower() not in ("null", "none", "-"):
                try: out["new_grids"] = int(g)
                except: out["new_grids"] = None
        elif line.startswith("COST_ESTIMATED:"):
            c = line.split(":", 1)[1].strip().lstrip("$")
            try: out["cost_estimated"] = float(c)
            except: out["cost_estimated"] = 0
        elif line.startswith("RISK_ASSESSMENT:"):
            out["risk"] = line.split(":", 1)[1].strip()
        elif line.startswith("NEXT_REVIEW:"):
            out["next_review"] = line.split(":", 1)[1].strip()

    # Also keep full output for context
    out["raw"] = claude_output
    return out


if __name__ == "__main__":
    # Test that Claude CLI is reachable
    r = invoke_claude("Say 'pong'")
    print(f"Return code: {r['returncode']}")
    print(f"Output: {r['output'][:200]}")
    print(f"Duration: {r['duration_sec']:.1f}s")
