# Mirrors the two CI matrix legs locally.
# Requires: make (Git for Windows ships it; or: choco install make / scoop install make)
#
# Targets:
#   make test         - Python base install only (.[dev])    - matches CI extras=dev
#   make test-mcp     - Python with MCP extras  (.[dev,mcp]) - matches CI extras=dev,mcp
#   make test-corp    - Python with CORP extras (.[dev,corp]) - matches CI extras=dev,corp
#   make test-vector  - Python with VECTOR extras (.[dev,vector]) - matches CI extras=dev,vector
#   make test-corp-mcp - Python with CORP and MCP extras (.[dev,corp,mcp]) - matches CI extras=dev,corp,mcp
#   make test-mcp-vector - Python with MCP and VECTOR extras (.[dev,mcp,vector]) - matches CI extras=dev,mcp,vector
#   make test-corp-vector - Python with CORP and VECTOR extras (.[dev,corp,vector]) - matches CI extras=dev,corp,vector
#   make test-corp-mcp-vector - Python with CORP, MCP, and VECTOR extras (.[dev,corp,mcp,vector]) - matches CI extras=dev,corp,mcp,vector
#   make test-vscode  - VS Code extension unit tests (vitest)
#   make test-all     - all nine in sequence
#   make clean        - remove any leftover venvs
#
# Each Python target creates a fresh isolated venv, runs tests, then removes it.
# Venvs are also gitignored as a belt-and-suspenders safety net.

SHELL        := pwsh.exe
.SHELLFLAGS  := -NoProfile -NonInteractive -Command

PKG_DIR    := SerenLoci
VSCODE_DIR := SerenLociVSCode
VENV_BASE  := .venv-base
VENV_MCP   := .venv-mcp
VENV_CORP  := .venv-corp
VENV_VECTOR := .venv-vector
VENV_CORP_MCP := .venv-corp-mcp
VENV_MCP_VECTOR := .venv-mcp-vector
VENV_CORP_VECTOR := .venv-corp-vector
VENV_CORP_MCP_VECTOR := .venv-corp-mcp-vector

.PHONY: test test-mcp test-corp test-corp-mcp test-mcp-vector test-corp-vector test-corp-mcp-vector test-vscode

test:
	Remove-Item -Recurse -Force $(VENV_BASE) -ErrorAction SilentlyContinue; \
	python -m venv $(VENV_BASE); \
	$$env:SETUPTOOLS_SCM_PRETEND_VERSION='0.0.0'; \
	.\.venv-base\Scripts\pip.exe install -e "$(PKG_DIR)/.[dev]"; \
	.\.venv-base\Scripts\python.exe -m pytest $(PKG_DIR)/tests/ -v; \
	$$status=$$LASTEXITCODE; \
	Remove-Item -Recurse -Force $(VENV_BASE) -ErrorAction SilentlyContinue; \
	exit $$status

test-mcp:
	Remove-Item -Recurse -Force $(VENV_MCP) -ErrorAction SilentlyContinue; \
	python -m venv $(VENV_MCP); \
	$$env:SETUPTOOLS_SCM_PRETEND_VERSION='0.0.0'; \
	.\.venv-mcp\Scripts\pip.exe install -e "$(PKG_DIR)/.[dev,mcp]"; \
	.\.venv-mcp\Scripts\python.exe -m pytest $(PKG_DIR)/tests/ -v; \
	$$status=$$LASTEXITCODE; \
	Remove-Item -Recurse -Force $(VENV_MCP) -ErrorAction SilentlyContinue; \
	exit $$status

test-corp:
	Remove-Item -Recurse -Force $(VENV_CORP) -ErrorAction SilentlyContinue; \
	python -m venv $(VENV_CORP); \
	$$env:SETUPTOOLS_SCM_PRETEND_VERSION='0.0.0'; \
	.\.venv-corp\Scripts\pip.exe install -e "$(PKG_DIR)/.[dev,corp]"; \
	.\.venv-corp\Scripts\python.exe -m pytest $(PKG_DIR)/tests/ -v; \
	$$status=$$LASTEXITCODE; \
	Remove-Item -Recurse -Force $(VENV_CORP) -ErrorAction SilentlyContinue; \
	exit $$status

test-corp-mcp:
	Remove-Item -Recurse -Force $(VENV_CORP_MCP) -ErrorAction SilentlyContinue; \
	python -m venv $(VENV_CORP_MCP); \
	$$env:SETUPTOOLS_SCM_PRETEND_VERSION='0.0.0'; \
	.\.venv-corp-mcp\Scripts\pip.exe install -e "$(PKG_DIR)/.[dev,corp,mcp]"; \
	.\.venv-corp-mcp\Scripts\python.exe -m pytest $(PKG_DIR)/tests/ -v; \
	$$status=$$LASTEXITCODE; \
	Remove-Item -Recurse -Force $(VENV_CORP_MCP) -ErrorAction SilentlyContinue; \
	exit $$status

test-mcp-vector:
	Remove-Item -Recurse -Force $(VENV_MCP_VECTOR) -ErrorAction SilentlyContinue; \
	python -m venv $(VENV_MCP_VECTOR); \
	$$env:SETUPTOOLS_SCM_PRETEND_VERSION='0.0.0'; \
	.\.venv-mcp-vector\Scripts\pip.exe install -e "$(PKG_DIR)/.[dev,mcp,vector]"; \
	.\.venv-mcp-vector\Scripts\python.exe -m pytest $(PKG_DIR)/tests/ -v; \
	$$status=$$LASTEXITCODE; \
	Remove-Item -Recurse -Force $(VENV_MCP_VECTOR) -ErrorAction SilentlyContinue; \
	exit $$status

test-corp-vector:
	Remove-Item -Recurse -Force $(VENV_CORP_VECTOR) -ErrorAction SilentlyContinue; \
	python -m venv $(VENV_CORP_VECTOR); \
	$$env:SETUPTOOLS_SCM_PRETEND_VERSION='0.0.0'; \
	.\.venv-corp-vector\Scripts\pip.exe install -e "$(PKG_DIR)/.[dev,corp,vector]"; \
	.\.venv-corp-vector\Scripts\python.exe -m pytest $(PKG_DIR)/tests/ -v; \
	$$status=$$LASTEXITCODE; \
	Remove-Item -Recurse -Force $(VENV_CORP_VECTOR) -ErrorAction SilentlyContinue; \
	exit $$status

test-corp-mcp-vector:
	Remove-Item -Recurse -Force $(VENV_CORP_MCP_VECTOR) -ErrorAction SilentlyContinue; \
	python -m venv $(VENV_CORP_MCP_VECTOR); \
	$$env:SETUPTOOLS_SCM_PRETEND_VERSION='0.0.0'; \
	.\.venv-corp-mcp-vector\Scripts\pip.exe install -e "$(PKG_DIR)/.[dev,corp,mcp,vector]"; \
	.\.venv-corp-mcp-vector\Scripts\python.exe -m pytest $(PKG_DIR)/tests/ -v; \
	$$status=$$LASTEXITCODE; \
	Remove-Item -Recurse -Force $(VENV_CORP_MCP_VECTOR) -ErrorAction SilentlyContinue; \
	exit $$status

test-vscode:
	npm --prefix $(VSCODE_DIR) install; \
	npm --prefix $(VSCODE_DIR) test; \
	exit $$LASTEXITCODE

test-all: test test-mcp test-corp test-corp-mcp test-mcp-vector test-corp-vector test-corp-mcp-vector test-vscode

clean:
	Remove-Item -Recurse -Force $(VENV_BASE), $(VENV_MCP), $(VENV_CORP), $(VENV_CORP_MCP), $(VENV_MCP_VECTOR), $(VENV_CORP_VECTOR), $(VENV_CORP_MCP_VECTOR) -ErrorAction SilentlyContinue
