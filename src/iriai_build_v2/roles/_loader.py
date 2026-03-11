from pathlib import Path


def load_prompt(module_file: str) -> str:
    return (Path(module_file).parent / "prompt.md").read_text()
