"""
Developer Toolbox FastAPI Server (v3.0.0 - The "Sin" Workshop)

OpenAPI-compliant server for development automation tasks. This version
introduces a workflow engine for orchestrating multi-step processes, a
persistent database for state management, and a dynamic tool registry
for self-improvement, embodying the core principles of the iA Sin project.

Author: Sin
Version: 3.0.0
"""
import os
import sys
import subprocess
import shutil
import logging
import json
import asyncio
import uuid
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List, Union, Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.openapi.utils import get_openapi
import uvicorn
from dotenv import load_dotenv
import aiosqlite

# ================================================
#                   Configuration
# ================================================
# Load environment variables from a .env file in the same directory
load_dotenv()

# --- Core Paths ---
# The absolute path for storing persistent data (database, logs, custom tools).
# CRITICAL: This must be set in your .env file for the server to start.
DATA_PATH = os.getenv("SIN_SERVER_DATA_PATH")
if not DATA_PATH or not os.path.isabs(DATA_PATH):
    raise ValueError("CRITICAL: SIN_SERVER_DATA_PATH environment variable must be set to an absolute path.")

DB_PATH = os.path.join(DATA_PATH, "dev_toolbox.db")
CUSTOM_TOOLS_PATH = os.path.join(DATA_PATH, "custom_tools")
LOG_PATH = os.path.join(DATA_PATH, "server.log")

# --- Security Configuration ---
# Load the API key from an environment variable for security.
API_KEY = os.getenv("DEV_TOOLBOX_API_KEY")
if not API_KEY:
    logging.warning("CRITICAL: DEV_TOOLBOX_API_KEY environment variable not set. Using a default, insecure key.")
    API_KEY = "insecure-default-key-for-testing-only"

# Load allowed origins for CORS. For production, this should be a comma-separated list.
ALLOWED_ORIGINS_STR = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS_STR.split(',')]

# --- Logging Setup ---
os.makedirs(DATA_PATH, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(module)s - %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)

# ================================================
#               Database Management
# ================================================
async def init_db():
    """Initializes the SQLite database and creates tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # Table for tracking managed projects
        await db.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                base_dir TEXT NOT NULL UNIQUE,
                status TEXT,
                created_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL,
                last_test_status TEXT
            )
        ''')
        # Table for tracking long-running workflows
        await db.execute('''
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                name TEXT,
                status TEXT,
                log TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        ''')
        # Table for registering dynamically created tools
        await db.execute('''
            CREATE TABLE IF NOT EXISTS custom_tools (
                name TEXT PRIMARY KEY,
                description TEXT,
                path TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
        ''')
        await db.commit()
    logging.info(f"Database initialized and verified at {DB_PATH}")

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
        """Initializes a Git repository and registers it in the project database."""
        repo_path = os.path.join(base_dir, repo_name)
        os.makedirs(repo_path, exist_ok=True)
        
        readme_path = os.path.join(repo_path, "README.md")
        if not os.path.exists(readme_path):
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(f"# {repo_name}\nProject initialized on {datetime.now().isoformat()}\n")
        
        self.run_command(["git", "init"], cwd=repo_path)
        self.run_command(["git", "add", "."], cwd=repo_path)
        self.run_command(["git", "commit", "-m", commit_msg], cwd=repo_path)

        project_id = str(uuid.uuid4())
        abs_repo_path = os.path.abspath(repo_path)
        current_time = datetime.utcnow().isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO projects (id, name, base_dir, status, created_at, last_updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, repo_name, abs_repo_path, "active", current_time, current_time)
            )
            await db.commit()
        
        logging.info(f"Project '{repo_name}' (ID: {project_id}) registered and initialized at {abs_repo_path}.")
        return {"status": "success", "project_id": project_id, "path": abs_repo_path}

    def setup_python_env(self, base_dir: str, requirements_file: str) -> Dict[str, str]:
        """Creates a Python venv and installs dependencies."""
        requirements_abs_path = os.path.join(base_dir, requirements_file)
        if not os.path.exists(requirements_abs_path):
            raise FileNotFoundError(f"Requirements file not found at: {requirements_abs_path}")

        venv_path = os.path.join(base_dir, "venv")
        self.run_command([sys.executable, "-m", "venv", venv_path], cwd=base_dir)
        pip_executable = os.path.join(venv_path, "Scripts" if os.name == "nt" else "bin", "pip")
        
        self.run_command([pip_executable, "install", "-r", requirements_file], cwd=base_dir)
        return {"status": "success", "message": f"Python venv created and dependencies installed.", "venv_path": os.path.abspath(venv_path)}

    def setup_node_env(self, base_dir: str, package_manager: str) -> Dict[str, str]:
        """Installs Node.js dependencies."""
        cmd = [package_manager, "install"]
        self.run_command(cmd, cwd=base_dir, shell=(os.name == 'nt'))
        return {"status": "success", "message": f"Node dependencies installed using {package_manager}."}

    def lint_code(self, base_dir: str, linter_type: str, config_file: Optional[str]) -> Dict[str, str]:
        """Runs a code linter."""
        cmd: List[str] = []
        use_shell = (os.name == 'nt')
        if linter_type == "eslint":
            cmd = ["npx", "eslint", "."]
            if config_file: cmd.extend(["--config", config_file])
        elif linter_type == "flake8":
            cmd = ["flake8", "."]
            if config_file: cmd.extend(["--config", config_file])
            use_shell = False
        
        output = self.run_command(cmd, cwd=base_dir, shell=use_shell)
        return {"status": "success", "output": output or "No linting issues found."}

    def format_code(self, base_dir: str, formatter_type: str) -> Dict[str, str]:
        """Formats code in-place."""
        cmd: List[str] = []
        use_shell = (os.name == 'nt')
        if formatter_type == "prettier":
            cmd = ["npx", "prettier", "--write", "."]
        elif formatter_type == "black":
            cmd = ["black", "."]
            use_shell = False
            
        output = self.run_command(cmd, cwd=base_dir, shell=use_shell)
        return {"status": "success", "output": output or "Formatting complete."}

    def backup_project(self, base_dir: str, destination_folder: str) -> Dict[str, str]:
        """Creates a backup of a project directory."""
        if not os.path.exists(base_dir):
            raise FileNotFoundError(f"Backup source directory not found: {base_dir}")
        
        os.makedirs(os.path.dirname(destination_folder), exist_ok=True)
        ignore = shutil.ignore_patterns('venv', 'node_modules', '.git', '__pycache__', '*.pyc', '.DS_Store', 'backups*')
        shutil.copytree(base_dir, destination_folder, dirs_exist_ok=True, ignore=ignore)
        
        abs_dest = os.path.abspath(destination_folder)
        return {"status": "success", "backup_path": abs_dest}

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

    async def create_custom_tool(self, tool_name: str, description: str, code: str) -> Dict[str, str]:
        """Saves a new Python script to the custom tools directory and registers it."""
        if not tool_name.isidentifier():
            raise ValueError("Tool name must be a valid Python identifier.")
        
        tool_path = os.path.join(CUSTOM_TOOLS_PATH, f"{tool_name}.py")
        os.makedirs(CUSTOM_TOOLS_PATH, exist_ok=True)
        with open(tool_path, "w", encoding="utf-8") as f:
            f.write(code)
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO custom_tools (name, description, path, created_at) VALUES (?, ?, ?, ?)",
                (tool_name, description, tool_path, datetime.utcnow().isoformat())
            )
            await db.commit()

        return {"status": "success", "message": f"Tool '{tool_name}' created and registered."}

    def execute_custom_tool(self, tool_name: str, params: Dict[str, Any]) -> str:
        """Executes a previously created custom tool by name."""
        tool_path = os.path.join(CUSTOM_TOOLS_PATH, f"{tool_name}.py")
        if not os.path.exists(tool_path):
            raise FileNotFoundError(f"Custom tool '{tool_name}' not found.")

        cmd = [sys.executable, tool_path]
        for key, value in params.items():
            cmd.append(f"--{key}")
            cmd.append(str(value))
        
        return self.run_command(cmd, cwd=DATA_PATH)

# ================================================
#            FastAPI App Initialization
# ================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Server starting up...")
    await init_db()
    yield
    logging.info("Server shutting down.")

app = FastAPI(
    title="Developer Toolbox API",
    description="An intelligent, stateful server for orchestrating complex development workflows.",
    version="3.0.0",
    lifespan=lifespan
)

# --- Security & CORS ---
security = HTTPBearer()
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.scheme != "Bearer" or credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True

app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# --- Singleton Tool Instance ---
tools_instance = DevTools()

# ================================================
#               API Request Models
# ================================================
class GitInitRequest(BaseModel):
    base_dir: str = Field(default=".", description="Base directory to run the command from.")
    repo_name: str = Field(..., description="Name of the Git repository (directory to be created).")
    commit_msg: Optional[str] = Field("Initial commit", description="Custom initial commit message.")

class PythonEnvRequest(BaseModel):
    base_dir: str = Field(..., description="Project directory where venv will be created.")
    requirements_file: str = Field(default="requirements.txt", description="Path to requirements.txt, relative to base_dir.")

class NodeEnvRequest(BaseModel):
    base_dir: str = Field(..., description="Project directory to run npm/yarn install in.")
    package_manager: str = Field(..., description="Node.js package manager ('npm' or 'yarn').")

class LintRequest(BaseModel):
    base_dir: str = Field(..., description="Directory to lint.")
    linter_type: str = Field(..., description="Linter to use ('eslint' or 'flake8').")
    config_file: Optional[str] = Field(None, description="Path to linter config file.")

class FormatRequest(BaseModel):
    base_dir: str = Field(..., description="Directory to format.")
    formatter_type: str = Field(..., description="Formatter to use ('prettier' or 'black').")

class BackupRequest(BaseModel):
    base_dir: str = Field(..., description="The source directory to back up.")
    destination_folder: Optional[str] = Field(None, description="Specific destination folder. If None, a timestamped folder is created.")

class RunTestsRequest(BaseModel):
    base_dir: str = Field(..., description="The project directory containing the tests to run.")
    project_id: str = Field(..., description="The ID of the project being tested, to update its status.")

class CreateToolRequest(BaseModel):
    tool_name: str = Field(..., description="A valid Python identifier for the tool name.")
    description: str = Field(..., description="A brief description of what the tool does.")
    python_code: str = Field(..., description="The full Python source code for the tool.")

class ExecuteToolRequest(BaseModel):
    tool_name: str
    params: Dict[str, Any] = Field(default_factory=dict)

class WorkflowStep(BaseModel):
    action: str = Field(..., description="The name of the action to execute (e.g., 'init_git_repo').")
    params: Dict[str, Any] = Field(..., description="A dictionary of parameters for the action.")
    on_failure: str = Field(default="halt", description="Behavior on failure ('halt' or 'continue').")

class WorkflowRequest(BaseModel):
    workflow_name: str
    steps: List[WorkflowStep]

# ================================================
#               Workflow Executor
# ================================================
async def run_workflow_background(workflow_id: str, steps: List[WorkflowStep]):
    """The core logic for running a workflow in the background."""
    log_entries = []
    
    async def log_and_update(message: str, status: Optional[str] = None):
        logging.info(f"[Workflow {workflow_id}] {message}")
        log_entries.append(f"[{datetime.utcnow().isoformat()}] {message}")
        update_query = "UPDATE workflows SET log = ? WHERE id = ?"
        params = [json.dumps(log_entries), workflow_id]
        if status:
            update_query = "UPDATE workflows SET status = ?, log = ? WHERE id = ?"
            params.insert(0, status)
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(update_query, tuple(params))
            await db.commit()

    await log_and_update("Workflow started.", "running")

    for i, step in enumerate(steps):
        await log_and_update(f"Executing step {i+1}/{len(steps)}: {step.action}")
        try:
            action_func = getattr(tools_instance, step.action, None)
            if not action_func:
                raise ValueError(f"Action '{step.action}' is not a valid tool.")
            
            if asyncio.iscoroutinefunction(action_func):
                result = await action_func(**step.params)
            else:
                result = await asyncio.to_thread(action_func, **step.params)

            await log_and_update(f"Step {step.action} completed successfully. Result: {json.dumps(result)}")
        except Exception as e:
            error_message = f"Step {step.action} failed: {str(e)}"
            await log_and_update(error_message, "failed")
            logging.error(f"[Workflow {workflow_id}] {error_message}", exc_info=True)
            if step.on_failure == "halt":
                await log_and_update("Halting workflow due to failure.")
                return

    await log_and_update("Workflow completed successfully.", "completed")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE workflows SET completed_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), workflow_id))
        await db.commit()

# ================================================
#                   API Endpoints
# ================================================

# --- Workflow Endpoints ---
@app.post("/workflows/start", summary="Start a New Workflow", operation_id="start_workflow")
async def api_start_workflow(request: WorkflowRequest, background_tasks: BackgroundTasks, authorized: bool = Depends(get_current_user)):
    workflow_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO workflows (id, name, status, log, created_at) VALUES (?, ?, ?, ?, ?)",
            (workflow_id, request.workflow_name, "pending", json.dumps([]), datetime.utcnow().isoformat())
        )
        await db.commit()
    
    background_tasks.add_task(run_workflow_background, workflow_id, request.steps)
    return {"status": "Workflow accepted", "workflow_id": workflow_id, "status_url": f"/workflows/status/{workflow_id}"}

@app.get("/workflows/status/{workflow_id}", summary="Get Workflow Status", operation_id="get_workflow_status")
async def api_get_workflow_status(workflow_id: str, authorized: bool = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    
    return {"id": row[0], "name": row[1], "status": row[2], "log": json.loads(row[3]), "created_at": row[4], "completed_at": row[5]}

# --- Action Endpoints ---
@app.post("/actions/init_git_repo", summary="Initialize Git Repository", operation_id="init_git_repo")
async def api_init_git_repo(request: GitInitRequest, authorized: bool = Depends(get_current_user)):
    try:
        return await tools_instance.init_git_repo(request.base_dir, request.repo_name, request.commit_msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/actions/run_tests", summary="Run Project Tests", operation_id="run_tests")
async def api_run_tests(request: RunTestsRequest, authorized: bool = Depends(get_current_user)):
    try:
        result = await asyncio.to_thread(tools_instance.run_tests, request.base_dir)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE projects SET last_test_status = ?, last_updated_at = ? WHERE id = ?",
                (result['status'], datetime.utcnow().isoformat(), request.project_id)
            )
            await db.commit()
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Self-Improvement Endpoints ---
@app.post("/actions/create_tool", summary="Create a Custom Tool", operation_id="create_tool")
async def api_create_tool(request: CreateToolRequest, authorized: bool = Depends(get_current_user)):
    try:
        return await tools_instance.create_custom_tool(request.tool_name, request.description, request.python_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/actions/execute_custom_tool", summary="Execute a Custom Tool", operation_id="execute_custom_tool")
async def api_execute_custom_tool(request: ExecuteToolRequest, authorized: bool = Depends(get_current_user)):
    try:
        output = await asyncio.to_thread(tools_instance.execute_custom_tool, request.tool_name, request.params)
        return {"status": "success", "output": output}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ================================================
#                   OpenAPI Customization
# ================================================
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    openapi_schema["info"]["x-openwebui"] = {
        "name": "Sin's Workshop",
        "icon": "https://raw.githubusercontent.com/devicons/devicon/master/icons/flask/flask-original.svg",
        "description": "An intelligent workshop for orchestrating development workflows and building new tools.",
        "category": "Development",
    }
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# ================================================
#                   Main Execution
# ================================================
if __name__ == "__main__":
    print("Starting Developer Toolbox API Server v3.0 (The Sin Workshop)...")
    if API_KEY == "insecure-default-key-for-testing-only":
        print("\nWARNING: Running with a default, insecure API key.")
        print("         Please set the DEV_TOOLBOX_API_KEY environment variable for production use.\n")
    
    print(f"Server data path: {DATA_PATH}")
    print(f"CORS is configured to allow origins: {ALLOWED_ORIGINS}")
    
    uvicorn.run(app, host="0.0.0.0", port=6700, log_level="info")