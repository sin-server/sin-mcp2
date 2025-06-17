"""
Developer Toolbox MCP Tool (v3.1.2 - Corrected for MCPO)

This script provides a command-line interface (CLI) to the DevTools class,
designed to be wrapped and served by an MCP Orchestrator like `mcpo`.

It uses Typer for clean command and argument parsing and includes the
correctly implemented 'serve' command for compatibility with the MCP lifecycle.

Author: Sin
Version: 3.1.2
"""
import os
import sys
import subprocess
import shutil
import logging
import json
import asyncio
from datetime import datetime
import typer
from typing_extensions import Annotated
from typing import Optional, List, Union, Dict, Any

# CORRECTED IMPORT: The serve function is located in the 'typer' submodule of 'mcp.cli'
from typer.main import serve

# ================================================
#      Core Tools Class (Stateless Actions)
# ================================================
class DevTools:
    """
    Main tool implementation, refactored to be stateless.
    Each method is a pure function that receives all necessary context as parameters.
    """

    def run_command(self, command: Union[List[str], str], cwd: str, shell: bool = False) -> str:
        """Executes a shell command and returns its standard output."""
        logging.info(f"Running command: {' '.join(command) if isinstance(command, list) else command} in '{cwd}'")
        try:
            result = subprocess.run(
                command, shell=shell, capture_output=True, text=True, check=True, cwd=cwd, encoding='utf-8'
            )
            if result.stdout:
                logging.info(f"Command stdout:\n{result.stdout.strip()}")
            return result.stdout.strip()
        except FileNotFoundError:
            cmd_name = command[0] if isinstance(command, list) else str(command).split()[0]
            error_msg = f"Command not found: '{cmd_name}'. Ensure it's installed and in the system's PATH."
            logging.error(error_msg)
            raise RuntimeError(error_msg)
        except subprocess.CalledProcessError as e:
            error_detail = e.stderr or e.stdout or "Command failed with no specific error output."
            logging.error(f"Command failed: {e.cmd}\nReturn Code: {e.returncode}\nError: {error_detail.strip()}")
            raise RuntimeError(f"Command execution failed: {error_detail.strip()}")
        except Exception as e:
            logging.error(f"An unexpected error occurred running command {command}: {e}")
            raise RuntimeError(f"Unexpected error running command: {e}")

    async def init_git_repo(self, base_dir: str, repo_name: str, commit_msg: str) -> Dict[str, Any]:
        """Initializes a Git repository."""
        repo_path = os.path.join(base_dir, repo_name)
        os.makedirs(repo_path, exist_ok=True)
        
        readme_path = os.path.join(repo_path, "README.md")
        if not os.path.exists(readme_path):
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(f"# {repo_name}\nProject initialized on {datetime.now().isoformat()}\n")
        
        self.run_command(["git", "init"], cwd=repo_path)
        self.run_command(["git", "add", "."], cwd=repo_path)
        self.run_command(["git", "commit", "-m", commit_msg], cwd=repo_path)
        
        logging.info(f"Git repository '{repo_name}' initialized at {repo_path}.")
        return {"status": "success", "message": "Git repository initialized.", "path": os.path.abspath(repo_path)}

    def setup_python_env(self, base_dir: str, requirements_file: str) -> Dict[str, str]:
        """Creates a Python venv and installs dependencies."""
        requirements_abs_path = os.path.join(base_dir, requirements_file)
        if not os.path.exists(requirements_abs_path):
            raise FileNotFoundError(f"Requirements file not found at: {requirements_abs_path}")

        venv_path = os.path.join(base_dir, "venv")
        self.run_command([sys.executable, "-m", "venv", venv_path], cwd=base_dir)
        pip_executable = os.path.join(venv_path, "Scripts" if os.name == "nt" else "bin", "pip")
        
        self.run_command([pip_executable, "install", "-r", requirements_file], cwd=base_dir)
        return {"status": "success", "message": "Python venv created and dependencies installed.", "venv_path": os.path.abspath(venv_path)}

    def run_tests(self, base_dir: str) -> Dict[str, str]:
        """Automatically detects and runs tests (pytest or npm test)."""
        if os.path.exists(os.path.join(base_dir, "pytest.ini")) or os.path.exists(os.path.join(base_dir, "pyproject.toml")):
            test_framework = "pytest"
            cmd = [sys.executable, "-m", "pytest"]
            shell = False
        elif os.path.exists(os.path.join(base_dir, "package.json")):
            test_framework = "npm test"
            cmd = ["npm", "test"]
            shell = (os.name == 'nt')
        else:
            raise FileNotFoundError("No recognizable test framework found (pytest/npm).")

        logging.info(f"Detected {test_framework}. Running tests...")
        try:
            output = self.run_command(cmd, cwd=base_dir, shell=shell)
            return {"status": "success", "test_type": test_framework, "output": output}
        except RuntimeError as e:
            return {"status": "failed", "test_type": test_framework, "output": str(e)}

    async def create_custom_tool(self, tool_name: str, description: str, code: str, custom_tools_path: str) -> Dict[str, str]:
        """Saves a new Python script to the custom tools directory."""
        if not tool_name.isidentifier():
            raise ValueError("Tool name must be a valid Python identifier.")
        
        tool_path = os.path.join(custom_tools_path, f"{tool_name}.py")
        os.makedirs(custom_tools_path, exist_ok=True)
        with open(tool_path, "w", encoding="utf-8") as f:
            f.write(code)
        
        return {"status": "success", "message": f"Tool '{tool_name}' created successfully.", "path": tool_path}

    def execute_custom_tool(self, tool_name: str, params: Dict[str, Any], custom_tools_path: str, data_path: str) -> str:
        """Executes a previously created custom tool by name."""
        tool_path = os.path.join(custom_tools_path, f"{tool_name}.py")
        if not os.path.exists(tool_path):
            raise FileNotFoundError(f"Custom tool '{tool_name}' not found.")

        cmd = [sys.executable, tool_path]
        for key, value in params.items():
            cmd.append(f"--{key}")
            cmd.append(str(value))
        
        return self.run_command(cmd, cwd=data_path)


# ================================================
#            CLI App and Commands
# ================================================
app = typer.Typer(
    name="dev-toolbox",
    help="A CLI for the Developer Toolbox, providing actions for Git, environments, and more."
)

tools_instance = DevTools()

# --- Environment Setup for CLI Commands ---
SIN_SERVER_DATA_PATH = os.getenv("SIN_SERVER_DATA_PATH", "/tmp/sin_dev_toolbox")
CUSTOM_TOOLS_PATH = os.path.join(SIN_SERVER_DATA_PATH, "custom_tools")

@app.command(name="init_git_repo", help="Initializes a new Git repository.")
def cli_init_git_repo(
    repo_name: Annotated[str, typer.Option(help="Name of the Git repository directory.")],
    base_dir: Annotated[str, typer.Option(help="Base directory to create the repo in.")] = ".",
    commit_msg: Annotated[str, typer.Option(help="Initial commit message.")] = "Initial commit"
):
    try:
        result = asyncio.run(tools_instance.init_git_repo(base_dir, repo_name, commit_msg))
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "detail": str(e)}), file=sys.stderr)
        raise typer.Exit(code=1)

@app.command(name="setup_python_env", help="Creates a Python venv and installs dependencies.")
def cli_setup_python_env(
    base_dir: Annotated[str, typer.Option(help="Project directory for the venv.")],
    requirements_file: Annotated[str, typer.Option(help="Path to requirements.txt.")] = "requirements.txt"
):
    try:
        result = tools_instance.setup_python_env(base_dir, requirements_file)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "detail": str(e)}), file=sys.stderr)
        raise typer.Exit(code=1)

@app.command(name="run_tests", help="Detects and runs the project's test suite.")
def cli_run_tests(
    base_dir: Annotated[str, typer.Option(help="The project directory to test.")]
):
    try:
        result = tools_instance.run_tests(base_dir)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "detail": str(e)}), file=sys.stderr)
        raise typer.Exit(code=1)

@app.command(name="create_tool", help="Creates and registers a new custom Python tool.")
def cli_create_tool(
    tool_name: Annotated[str, typer.Option(help="A valid Python identifier for the tool.")],
    description: Annotated[str, typer.Option(help="A short description of the tool.")],
    code_path: Annotated[str, typer.Option(help="Path to the .py file containing the tool's code.")]
):
    try:
        with open(code_path, 'r', encoding='utf-8') as f:
            python_code = f.read()
        result = asyncio.run(tools_instance.create_custom_tool(tool_name, description, python_code, CUSTOM_TOOLS_PATH))
        print(json.dumps(result, indent=2))
    except FileNotFoundError:
        print(json.dumps({"status": "error", "detail": f"Code file not found at {code_path}"}), file=sys.stderr)
        raise typer.Exit(code=1)
    except Exception as e:
        print(json.dumps({"status": "error", "detail": str(e)}), file=sys.stderr)
        raise typer.Exit(code=1)

@app.command(name="execute_custom_tool", help="Executes a previously created custom tool.")
def cli_execute_custom_tool(
    tool_name: Annotated[str, typer.Option(help="The name of the custom tool to run.")],
    params_json: Annotated[str, typer.Option(help="A JSON string of parameters, e.g., '{\"key\":\"value\"}'.")] = "{}"
):
    try:
        params = json.loads(params_json)
        output = tools_instance.execute_custom_tool(tool_name, params, CUSTOM_TOOLS_PATH, SIN_SERVER_DATA_PATH)
        print(json.dumps({"status": "success", "output": output}, indent=2))
    except json.JSONDecodeError:
        print(json.dumps({"status": "error", "detail": "Invalid JSON in params_json string."}), file=sys.stderr)
        raise typer.Exit(code=1)
    except Exception as e:
        print(json.dumps({"status": "error", "detail": str(e)}), file=sys.stderr)
        raise typer.Exit(code=1)

# --- The Critical Serve Command for MCP ---
@app.command(name="serve", help="Serves the Typer application for MCP Orchestrator.", hidden=True)
def cli_serve():
    """
    This special command is used by the mcpo server to wrap the CLI.
    It uses the mcp.cli.typer.serve utility to handle the stdio communication.
    """
    # CORRECTED FUNCTION CALL
    serve(app)

@app.command()
def hello(name: str):
    typer.echo(f"Hello {name}")

if __name__ == "__main__":
    # This block allows the script to be executed directly from the command line,
    # making it a standard, runnable CLI tool.
    app()