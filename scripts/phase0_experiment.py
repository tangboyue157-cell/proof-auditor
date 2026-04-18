import os
import re
from pathlib import Path

from core.ai_client import AIClient
from core.compiler import compile_lean_file

# Paths
ROOT_DIR = Path(__file__).parent.parent
PROMPT_FILE = ROOT_DIR / "agents" / "translator.md"
CORRECT_FILE = ROOT_DIR / "benchmark" / "phase0" / "correct_proof.txt"
BUGGY_FILE = ROOT_DIR / "benchmark" / "phase0" / "buggy_proof.txt"
LEAN_CORRECT = ROOT_DIR / "ProofAuditor" / "Workspace" / "Correct.lean"
LEAN_BUGGY = ROOT_DIR / "ProofAuditor" / "Workspace" / "Buggy.lean"


def extract_lean_code(resp: str) -> str:
    """Extract lean code from markdown codeblocks."""
    pattern = re.compile(r"```(?:lean)?\n(.*?)\n```", re.DOTALL)
    matches = pattern.findall(resp)
    if matches:
        return matches[0]
    return resp  # Fallback if no code blocks


def translate_proof(client: AIClient, system_prompt: str, proof_text: str) -> str:
    print("Translating proof via AI...")
    user_prompt = f"""
Here is the proof to translate. Please output ONLY the raw Lean 4 code inside ```lean ... ``` blocks.
Do not output translation_map.yaml for this quick test. Ensure you use `sorry` mapping to the numbered steps.
Include proper imports like `import Mathlib`. Do not try to prove it, just faithfully translate!

{proof_text}
"""
    client.system_prompt = system_prompt
    resp = client.chat(user_prompt)
    return extract_lean_code(resp.content)


def main():
    print("=== Phase 0: Concept Validation ===")
    os.makedirs(LEAN_CORRECT.parent, exist_ok=True)
    
    with open(PROMPT_FILE) as f:
        system_prompt = f.read()

    client = AIClient(provider="openai", model="gpt-5.4")

    # 1. Correct Proof
    with open(CORRECT_FILE) as f:
        correct_txt = f.read()
    
    print("\n--- Translating Correct Proof ---")
    correct_lean = translate_proof(client, system_prompt, correct_txt)
    with open(LEAN_CORRECT, "w") as f:
        f.write(correct_lean)
    
    print("Compiling Correct.lean...")
    res_correct = compile_lean_file("ProofAuditor/Workspace/Correct.lean", str(ROOT_DIR))
    print(f"Diagnostics: {len(res_correct.diagnostics)}")
    print(f"Sorry count: {len(res_correct.sorry_locations)}")
    for s in res_correct.sorry_locations:
        print(f"  Line {s.line}")

    # 2. Buggy Proof
    with open(BUGGY_FILE) as f:
        buggy_txt = f.read()

    print("\n--- Translating Buggy Proof ---")
    buggy_lean = translate_proof(client, system_prompt, buggy_txt)
    with open(LEAN_BUGGY, "w") as f:
        f.write(buggy_lean)

    print("Compiling Buggy.lean...")
    res_buggy = compile_lean_file("ProofAuditor/Workspace/Buggy.lean", str(ROOT_DIR))
    print(f"Diagnostics: {len(res_buggy.diagnostics)}")
    print(f"Sorry count: {len(res_buggy.sorry_locations)}")
    for s in res_buggy.sorry_locations:
        print(f"  Line {s.line}")

if __name__ == "__main__":
    main()
