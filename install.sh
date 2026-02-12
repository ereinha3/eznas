#!/bin/bash
#
# NAS Orchestrator - One-Command Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/yourusername/nas-orchestrator/main/install.sh | bash
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO_URL="https://github.com/yourusername/nas-orchestrator.git"
INSTALL_DIR="${HOME}/nas-orchestrator"
APP_DATA_DIR="${HOME}/.config/nas-orchestrator"
PORT="8443"

# Print functions
print_header() {
    echo -e "${BLUE}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║           NAS Orchestrator - One-Command Setup               ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check prerequisites
check_prerequisites() {
    print_step "Checking prerequisites..."
    
    local missing_deps=()
    
    if ! command_exists docker; then
        missing_deps+=("docker")
    fi
    
    if ! command_exists docker-compose && ! docker compose version >/dev/null 2>&1; then
        missing_deps+=("docker-compose")
    fi
    
    if ! command_exists git; then
        missing_deps+=("git")
    fi
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        print_error "Missing required dependencies: ${missing_deps[*]}"
        echo ""
        echo "Please install the missing dependencies:"
        echo "  - Docker: https://docs.docker.com/get-docker/"
        echo "  - Docker Compose: https://docs.docker.com/compose/install/"
        echo "  - Git: https://git-scm.com/downloads"
        echo ""
        exit 1
    fi
    
    print_success "All prerequisites found"
}

# Detect Docker Compose command
detect_docker_compose() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
    elif command_exists docker-compose; then
        echo "docker-compose"
    else
        print_error "Docker Compose not found"
        exit 1
    fi
}

# Clone repository
clone_repository() {
    print_step "Cloning NAS Orchestrator repository..."
    
    if [ -d "$INSTALL_DIR" ]; then
        print_warning "Directory $INSTALL_DIR already exists"
        read -p "Do you want to update it? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            cd "$INSTALL_DIR"
            git pull origin main
            print_success "Repository updated"
        else
            print_warning "Using existing installation"
        fi
    else
        git clone "$REPO_URL" "$INSTALL_DIR"
        print_success "Repository cloned to $INSTALL_DIR"
    fi
}

# Create necessary directories
setup_directories() {
    print_step "Setting up directories..."
    
    mkdir -p "$APP_DATA_DIR"
    mkdir -p "$INSTALL_DIR/generated"
    mkdir -p "$INSTALL_DIR/.secrets"
    
    print_success "Directories created"
}

# Build and start the orchestrator
start_orchestrator() {
    print_step "Building and starting NAS Orchestrator..."
    
    cd "$INSTALL_DIR"
    
    DOCKER_COMPOSE=$(detect_docker_compose)
    
    # Build the image
    print_step "Building Docker image (this may take a few minutes)..."
    $DOCKER_COMPOSE -f docker-compose.bootstrap.yml build
    
    # Start the orchestrator
    print_step "Starting services..."
    $DOCKER_COMPOSE -f docker-compose.bootstrap.yml up -d
    
    print_success "NAS Orchestrator is starting up..."
}

# Wait for service to be ready
wait_for_service() {
    print_step "Waiting for NAS Orchestrator to be ready..."
    
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if curl -fs "http://localhost:$PORT/api/setup/status" >/dev/null 2>&1; then
            print_success "NAS Orchestrator is ready!"
            return 0
        fi
        
        echo -n "."
        sleep 2
        ((attempt++))
    done
    
    print_error "Timed out waiting for NAS Orchestrator to start"
    print_error "Check logs with: docker logs nas-orchestrator"
    return 1
}

# Display access information
show_access_info() {
    local ip_address
    ip_address=$(hostname -I | awk '{print $1}')
    
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                    Setup Complete!                           ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${YELLOW}Access the NAS Orchestrator UI at:${NC}"
    echo ""
    echo -e "  ${GREEN}Local:${NC}   http://localhost:$PORT"
    echo -e "  ${GREEN}Network:${NC} http://$ip_address:$PORT"
    echo ""
    echo -e "${BLUE}First-time Setup Instructions:${NC}"
    echo "  1. Open the URL above in your browser"
    echo "  2. Create your admin account"
    echo "  3. Configure your storage paths"
    echo "  4. Review and apply your configuration"
    echo ""
    echo -e "${YELLOW}Important:${NC} Make sure port $PORT is open in your firewall"
    echo ""
    echo -e "${BLUE}Useful Commands:${NC}"
    echo "  View logs:     docker logs -f nas-orchestrator"
    echo "  Stop:          cd $INSTALL_DIR && $(detect_docker_compose) -f docker-compose.bootstrap.yml down"
    echo "  Restart:       cd $INSTALL_DIR && $(detect_docker_compose) -f docker-compose.bootstrap.yml restart"
    echo ""
    echo -e "${GREEN}Happy orchestrating! 🚀${NC}"
    echo ""
}

# Main installation flow
main() {
    print_header
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dir)
                INSTALL_DIR="$2"
                shift 2
                ;;
            --port)
                PORT="$2"
                shift 2
                ;;
            --appdata)
                APP_DATA_DIR="$2"
                shift 2
                ;;
            --help)
                echo "Usage: install.sh [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --dir DIR       Installation directory (default: $HOME/nas-orchestrator)"
                echo "  --port PORT     Port to run on (default: 8443)"
                echo "  --appdata DIR   AppData directory (default: $HOME/.config/nas-orchestrator)"
                echo "  --help          Show this help message"
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done
    
    echo "Installation directory: $INSTALL_DIR"
    echo "Port: $PORT"
    echo "AppData: $APP_DATA_DIR"
    echo ""
    
    # Run installation steps
    check_prerequisites
    clone_repository
    setup_directories
    
    # Update port in docker-compose if different
    if [ "$PORT" != "8443" ]; then
        print_step "Configuring custom port..."
        sed -i "s/8443:8443/$PORT:8443/g" "$INSTALL_DIR/docker-compose.bootstrap.yml"
    fi
    
    start_orchestrator
    wait_for_service
    show_access_info
}

# Run main function
main "$@"
