#!/bin/bash
# Development helper script
# Usage: ./scripts/dev.sh [command]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Load .env if exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

case "${1:-help}" in
    up)
        # Start dev environment (orchestrator + frontend with hot reload)
        echo "Starting development environment..."
        docker compose -f docker-compose.dev.yml up --build
        ;;

    up-full)
        # Start dev environment with all media services
        echo "Starting full development environment with media services..."
        docker compose -f docker-compose.dev.yml --profile full up --build
        ;;

    up-pipeline)
        # Start dev environment with pipeline worker
        echo "Starting development environment with pipeline worker..."
        docker compose -f docker-compose.dev.yml --profile pipeline up --build
        ;;

    down)
        # Stop all dev containers
        echo "Stopping development environment..."
        docker compose -f docker-compose.dev.yml --profile full --profile pipeline down
        ;;

    logs)
        # Follow logs
        docker compose -f docker-compose.dev.yml logs -f "${2:-}"
        ;;

    shell)
        # Open shell in orchestrator container
        docker compose -f docker-compose.dev.yml exec orchestrator bash
        ;;

    local)
        # Run locally without Docker (requires Python venv and Node)
        echo "Starting local development (no Docker)..."
        echo ""
        echo "Terminal 1: Backend"
        echo "  source .venv/bin/activate"
        echo "  uvicorn orchestrator.app:app --reload --port 8443"
        echo ""
        echo "Terminal 2: Frontend"
        echo "  cd frontend && npm run dev"
        echo ""
        echo "Or run both with:"
        echo "  ./scripts/dev.sh local-start"
        ;;

    local-start)
        # Start local dev servers (requires tmux or run in separate terminals)
        if command -v tmux &> /dev/null; then
            tmux new-session -d -s dev "source .venv/bin/activate && uvicorn orchestrator.app:app --reload --port 8443"
            tmux split-window -h -t dev "cd frontend && VITE_API_ORIGIN=http://localhost:8443 npm run dev"
            tmux attach -t dev
        else
            echo "tmux not found. Please run in separate terminals:"
            echo ""
            echo "Terminal 1: source .venv/bin/activate && uvicorn orchestrator.app:app --reload --port 8443"
            echo "Terminal 2: cd frontend && VITE_API_ORIGIN=http://localhost:8443 npm run dev"
        fi
        ;;

    test)
        # Run tests
        source .venv/bin/activate
        pytest "${@:2}"
        ;;

    lint)
        # Run linters
        source .venv/bin/activate
        ruff check orchestrator/
        mypy orchestrator/
        cd frontend && npm run lint
        ;;

    help|*)
        echo "NAS Orchestrator Development Helper"
        echo ""
        echo "Usage: ./scripts/dev.sh [command]"
        echo ""
        echo "Docker-based development:"
        echo "  up          Start orchestrator + frontend (hot reload)"
        echo "  up-full     Start with all media services"
        echo "  up-pipeline Start with pipeline worker"
        echo "  down        Stop all dev containers"
        echo "  logs [svc]  Follow container logs"
        echo "  shell       Open shell in orchestrator container"
        echo ""
        echo "Local development (no Docker):"
        echo "  local       Show instructions for local dev"
        echo "  local-start Start local servers (requires tmux)"
        echo ""
        echo "Quality:"
        echo "  test        Run pytest"
        echo "  lint        Run ruff, mypy, eslint"
        ;;
esac
