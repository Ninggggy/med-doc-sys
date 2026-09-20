import subprocess


class TerminalTool:
    def run(self, command: str, timeout: int = 10):
        proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
