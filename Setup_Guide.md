# How to Set Up SecurityCodeWiki on a Completely New Machine

This guide explains how to download and run SecurityCodeWiki from GitHub on Windows or macOS.

SecurityCodeWiki has two parts:

1. The **backend**, which runs with Python.
2. The **frontend**, which opens in your web browser and runs with Node.js.

Both parts must be running at the same time.

# Windows setup

## Step 1: Install Git

1. Open <https://git-scm.com/download/win>.
2. Download Git for Windows.
3. Run the installer.
4. Keep the default options unless you know you need something different.
5. Close and reopen PowerShell after installation.

To check Git, open **PowerShell** and run:

```powershell
git --version
```

You should see a Git version number.

## Step 2: Install Python

1. Open <https://www.python.org/downloads/windows/>.
2. Download a current Python 3 release.
3. Start the installer.
4. Select **Add Python to PATH** before clicking Install.
5. Close and reopen PowerShell after installation.

Check Python:

```powershell
python --version
```

If Windows opens the Microsoft Store instead, install Python with:

```powershell
winget install --id Python.Python.3.13 -e
```

Then close and reopen PowerShell.

## Step 3: Install Node.js

1. Open <https://nodejs.org/>.
2. Download the **LTS** version.
3. Run the installer with the default options.
4. Close and reopen PowerShell.

Check Node.js and npm:

```powershell
node --version
npm --version
```

Both commands should show version numbers.

## Step 4: Download SecurityCodeWiki from GitHub

Choose a location where you want to keep the project, such as your Downloads folder:

```powershell
cd $HOME\Downloads
git clone https://github.com/sherryvoid/SmartSecurityWiki.git
cd SmartSecurityWiki
```

You should now be inside the project folder.

## Step 5: Create the Python virtual environment

Run this from the `SmartSecurityWiki` folder:

```powershell
python -m venv .venv
```

The `.venv` folder contains the project's private Python installation. Do not delete it while using the project.

You may activate it with:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell says script execution is disabled, that is okay. Activation is optional. The commands below use the virtual environment directly.

## Step 6: Install backend packages

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

This can take several minutes. The embedding and machine-learning packages are large.

## Step 7: Create your local environment file

Run:

```powershell
Copy-Item backend\.env.example backend\.env
notepad backend\.env
```

In Notepad, change these values:

```text
APP_SUPERUSER_USERNAME=admin
APP_SUPERUSER_PASSWORD=choose_your_own_password
APP_SECRET_KEY=replace_with_a_long_random_secret_value
```

Remember the username and password because you will use them to log in.

Do not share or upload `backend/.env`. It may contain passwords and API keys.

You can leave all AI provider keys empty for the first startup. Save the file and close Notepad.

## Step 8: Install frontend packages

Run:

```powershell
cd frontend
npm ci
cd ..
```

## Step 9: Start the backend

Use the current PowerShell window:

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Keep this window open. The backend is ready when you see a message similar to:

```text
Uvicorn running on http://127.0.0.1:8000
```

## Step 10: Start the frontend

Open a **second PowerShell window**. Run:

```powershell
cd $HOME\Downloads\SmartSecurityWiki\frontend
npm run dev
```

Keep this second window open too.

## Step 11: Open SecurityCodeWiki

Open this address in Chrome, Edge, or Firefox:

<http://127.0.0.1:5173>

Log in with the username and password you put in `backend/.env`.

## Step 12: Check that everything works

Backend health:

<http://127.0.0.1:8000/api/health>

You should see:

```json
{"status":"ok"}
```

You can now add a repository URL on the SecurityCodeWiki home page.

---

# macOS setup

## Step 1: Open Terminal

Press `Command + Space`, type `Terminal`, and press Enter.

## Step 2: Install Homebrew

Homebrew helps install Git, Python, and Node.js. In Terminal, run:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the instructions shown by Homebrew. It may ask for your Mac password.

At the end, Homebrew may display one or two commands that add Homebrew to your PATH. Copy and run those commands before continuing.

Check Homebrew:

```bash
brew --version
```

## Step 3: Install Git, Python, and Node.js

Run:

```bash
brew install git python node
```

Check each tool:

```bash
git --version
python3 --version
node --version
npm --version
```

Each command should show a version number.

## Step 4: Download SecurityCodeWiki from GitHub

Run:

```bash
cd ~/Downloads
git clone https://github.com/sherryvoid/SmartSecurityWiki.git
cd SmartSecurityWiki
```

## Step 5: Create and activate the Python virtual environment

Run:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

When activation works, your Terminal prompt usually starts with `(.venv)`.

## Step 6: Install backend packages

Run:

```bash
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

This can take several minutes.

## Step 7: Create your local environment file

Run:

```bash
cp backend/.env.example backend/.env
open -e backend/.env
```

TextEdit will open the file. Change these values:

```text
APP_SUPERUSER_USERNAME=admin
APP_SUPERUSER_PASSWORD=choose_your_own_password
APP_SECRET_KEY=replace_with_a_long_random_secret_value
```

Remember your username and password. Save the file and close TextEdit.

Do not share or upload `backend/.env`. You may leave all AI provider keys empty for the first startup.

## Step 8: Install frontend packages

Run:

```bash
cd frontend
npm ci
cd ..
```

## Step 9: Start the backend

Run:

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Keep this Terminal window open.

## Step 10: Start the frontend

Open a **second Terminal window** and run:

```bash
cd ~/Downloads/SmartSecurityWiki/frontend
npm run dev
```

Keep this window open too.

## Step 11: Open and log in

Open this address in a browser:

<http://127.0.0.1:5173>

Log in with the username and password from `backend/.env`.

Check backend health at:

<http://127.0.0.1:8000/api/health>

---

# Optional: enable an AI model

SecurityCodeWiki starts without an AI model, but Ask, Compare, and Security Wiki generation need one.

You can use a cloud provider or Ollama. You only need to configure the provider you want.

## Option A: OpenRouter and GPT-5.1

1. Create an account at <https://openrouter.ai/>.
2. Create an API key.
3. Open `backend/.env`.
4. Set:

```text
OPENROUTER_API_KEY=paste_your_key_here
OPENROUTER_MODEL=openai/gpt-5.1
```

5. Stop and restart the backend after changing `.env`.

Never commit or share the API key.

## Option B: Google Gemini

Add your Gemini key to `backend/.env`:

```text
GEMINI_API_KEY=paste_your_key_here
GEMINI_DEFAULT_MODEL=gemini-2.5-flash
```

Restart the backend.

## Option C: Groq

Add your Groq key to `backend/.env`:

```text
GROQ_API_KEY=paste_your_key_here
GROQ_DEFAULT_MODEL=openai/gpt-oss-20b
GROQ_ACTIVE_MODELS=openai/gpt-oss-20b
```

Restart the backend.

## Option D: local Ollama

Ollama keeps model inference on your machine, but local models can require a lot of disk space and memory.

Install Ollama from <https://ollama.com/>. Then install the exact configured model manually:

```bash
ollama pull qwen3.5:9b
```

Ensure `backend/.env` contains:

```text
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=qwen3.5:9b
```

SecurityCodeWiki never downloads a model automatically and never replaces an unavailable model with another model.

---

# How to start the tool later

You only install the packages once. On later days, start the two servers again.

## Windows

PowerShell window 1:

```powershell
cd $HOME\Downloads\SmartSecurityWiki\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

PowerShell window 2:

```powershell
cd $HOME\Downloads\SmartSecurityWiki\frontend
npm run dev
```

## macOS

Terminal window 1:

```bash
cd ~/Downloads/SmartSecurityWiki/backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Terminal window 2:

```bash
cd ~/Downloads/SmartSecurityWiki/frontend
npm run dev
```

Then open <http://127.0.0.1:5173>.

To stop either server, click its Terminal or PowerShell window and press `Control + C`.

---

# How to update from GitHub later

First stop the backend and frontend. Then open Terminal or PowerShell in the project folder and run:

```bash
git pull
```

Update backend packages:

Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

macOS:

```bash
./.venv/bin/python -m pip install -r backend/requirements.txt
```

Update frontend packages:

```bash
cd frontend
npm ci
cd ..
```

Start both servers again.

---

# Common problems

## `No module named 'app'`

The backend was started from the wrong folder. Change into `backend` first:

Windows:

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

macOS:

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Port 8000 or 5173 is already in use

Another copy may already be running. Look for an old Terminal or PowerShell window and press `Control + C`. Then try again.

## The frontend opens but cannot reach the backend

Confirm that the backend window is still running. Open <http://127.0.0.1:8000/api/health>. If it does not show `{"status":"ok"}`, restart the backend.

## A model says unavailable

This does not mean the whole application is broken.

- For a cloud provider, check that its API key is in `backend/.env` and restart the backend.
- For Ollama, start Ollama and run `ollama list` to see installed models.
- The installed Ollama model name must exactly match `OLLAMA_DEFAULT_MODEL`.
- SecurityCodeWiki will not silently switch to another model.


---


