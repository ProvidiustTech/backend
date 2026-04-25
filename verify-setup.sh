#!/bin/bash
# verify-setup.sh - Verify Providius configuration and setup

set -e

echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║ Providius — Configuration Verification                                ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check_file() {
    if [ -f "$1" ]; then
        echo -e "${GREEN}✓${NC} $1"
        return 0
    else
        echo -e "${RED}✗${NC} $1"
        return 1
    fi
}

check_command() {
    if command -v "$1" &> /dev/null; then
        echo -e "${GREEN}✓${NC} $1"
        return 0
    else
        echo -e "${RED}✗${NC} $1"
        return 1
    fi
}

ERRORS=0

# Check critical files
echo "Critical Files:"
check_file ".env" || ERRORS=$((ERRORS + 1))
check_file "docker-compose.yml" || ERRORS=$((ERRORS + 1))
check_file "Dockerfile" || ERRORS=$((ERRORS + 1))
check_file "pyproject.toml" || ERRORS=$((ERRORS + 1))
check_file "Makefile" || ERRORS=$((ERRORS + 1))
check_file "providius-dashboard/package.json" || ERRORS=$((ERRORS + 1))
check_file "providius-dashboard/next.config.js" || ERRORS=$((ERRORS + 1))
check_file "providius-dashboard/lib/api.ts" || ERRORS=$((ERRORS + 1))
check_file "providius-dashboard/lib/conversations.ts" || ERRORS=$((ERRORS + 1))
echo ""

# Check commands
echo "Required Commands:"
check_command "docker" || ERRORS=$((ERRORS + 1))
check_command "docker" || ERRORS=$((ERRORS + 1))
check_command "node" || ERRORS=$((ERRORS + 1))
check_command "make" || ERRORS=$((ERRORS + 1))
echo ""

# Check environment
echo "Environment Variables (.env):"
if [ -f .env ]; then
    if grep -q "POSTGRES_HOST" .env; then
        echo -e "${GREEN}✓${NC} POSTGRES_HOST configured"
    else
        echo -e "${YELLOW}⚠${NC} POSTGRES_HOST missing"
    fi
    
    if grep -q "LLM_PROVIDER" .env; then
        LLM=$(grep "LLM_PROVIDER=" .env | cut -d'=' -f2)
        echo -e "${GREEN}✓${NC} LLM_PROVIDER: $LLM"
    else
        echo -e "${YELLOW}⚠${NC} LLM_PROVIDER not set"
    fi
    
    if grep -q "SECRET_KEY" .env; then
        echo -e "${GREEN}✓${NC} SECRET_KEY configured"
    else
        echo -e "${RED}✗${NC} SECRET_KEY missing"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo -e "${RED}✗${NC} .env file not found"
    ERRORS=$((ERRORS + 1))
fi
echo ""

# Check Python packages (if uv is available)
echo "Python Dependencies:"
if command -v uv &> /dev/null; then
    check_command "python3" || ERRORS=$((ERRORS + 1))
else
    echo -e "${YELLOW}⚠${NC} uv not found (optional)"
fi
echo ""

# Check Node.js project
echo "Frontend Setup:"
if [ -f "providius-dashboard/package.json" ]; then
    echo -e "${GREEN}✓${NC} package.json found"
    if [ -d "providius-dashboard/node_modules" ]; then
        echo -e "${GREEN}✓${NC} node_modules installed"
    else
        echo -e "${YELLOW}⚠${NC} node_modules not installed (run: cd providius-dashboard && npm install)"
    fi
else
    echo -e "${RED}✗${NC} package.json not found"
    ERRORS=$((ERRORS + 1))
fi
echo ""

# Summary
echo "╔════════════════════════════════════════════════════════════════════════╗"
if [ $ERRORS -eq 0 ]; then
    echo -e "║ ${GREEN}✓ All checks passed!${NC}                                                     ║"
    echo "╠════════════════════════════════════════════════════════════════════════╣"
    echo "║ Next steps:                                                            ║"
    echo "║  1. Start services: make up                                           ║"
    echo "║  2. Or run with frontend: cd providius-dashboard && npm run dev       ║"
    echo "║  3. Check health: make health                                         ║"
else
    echo -e "║ ${RED}✗ Found $ERRORS issue(s)${NC}                                                   ║"
fi
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""

exit $ERRORS
