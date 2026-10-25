#!/usr/bin/env bash
# Clone the two external environment repos at the exact pinned commits this
# project was built against, then apply this project's local additions to the
# SHADE-Arena checkout. The external repos are large (~385MB + ~538MB) and are
# NOT vendored into this repo.
#
# IMPORTANT: the code path-hacks to `environments/control_arena` and
# `environments/shade_arena` RELATIVE TO THE REPO ROOT (see
# multiagent_harness/shade_bootstrap.py and multiagent_harness/ca_adapter.py).
# They MUST live at exactly environments/<name> under the repo root.
#
# Run this from the repo root AFTER creating your venv and installing
# requirements.txt.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVDIR="$ROOT/environments"
mkdir -p "$ENVDIR"

SHADE_URL="https://github.com/jkutaso/SHADE-Arena"
SHADE_COMMIT="6614e77191c1f8e2d0c44c123c6f915905ebafae"
CA_URL="https://github.com/UKGovernmentBEIS/control-arena"
CA_COMMIT="10860116c370c74c56d6d49edce629b62cf5661b"

clone_pinned () {
  local url="$1" commit="$2" dest="$3"
  if [ -d "$dest/.git" ]; then
    echo "[install_deps] $dest already present; fetching + checking out pin"
    git -C "$dest" fetch --all --quiet
  else
    echo "[install_deps] cloning $url -> $dest"
    git clone "$url" "$dest"
  fi
  git -C "$dest" checkout "$commit"
  echo "[install_deps] $dest @ $(git -C "$dest" rev-parse --short HEAD)"
}

clone_pinned "$SHADE_URL" "$SHADE_COMMIT" "$ENVDIR/shade_arena"
clone_pinned "$CA_URL"    "$CA_COMMIT"    "$ENVDIR/control_arena"

# ---------------------------------------------------------------------------
# Apply this project's local additions to the SHADE-Arena checkout.
# The harness calls get_appropriate_llm(model, org="openrouter"), which only
# works if the OpenRouter provider is present (SHADE's llms/__init__.py
# auto-imports and auto-registers every provider module in llms/). The pinned
# upstream commit has NO OpenRouter provider, so we add it, plus a small
# robustness fix and the openrouter org->model mapping in config.yaml.
# ---------------------------------------------------------------------------
PATCH_DIR="$ROOT/setup/shade_patch"
echo "[install_deps] adding OpenRouter provider -> environments/shade_arena/llms/openrouter_llm.py"
cp "$PATCH_DIR/llms/openrouter_llm.py" "$ENVDIR/shade_arena/llms/openrouter_llm.py"
echo "[install_deps] applying local edits (config.yaml model map + together_llm.py fix)"
if git -C "$ENVDIR/shade_arena" apply --check "$PATCH_DIR/shade_arena_local_edits.patch" 2>/dev/null; then
  git -C "$ENVDIR/shade_arena" apply "$PATCH_DIR/shade_arena_local_edits.patch"
  echo "[install_deps] local edits applied"
else
  echo "[install_deps] WARNING: patch did not apply cleanly (upstream may differ)."
  echo "              Manually add an 'openrouter:' block under org_model_mapping in"
  echo "              environments/shade_arena/config.yaml (see setup/shade_patch/)."
fi

cat <<'NOTE'

[install_deps] Done.
  - SHADE-Arena task data (bank_transfer_2, api_key_calendar_agendas_2) ships
    inside environments/shade_arena; no extra download is needed.
  - The OpenRouter provider (llms/openrouter_llm.py) reads OPENROUTER_API_KEY
    from the environment only; it contains no secrets.
  - Set OPENROUTER_API_KEY (and OPENAI_API_KEY / OPENAI_BASE_URL for the
    ControlArena monitor) before running anything. See the README.
NOTE
