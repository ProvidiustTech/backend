#!/bin/bash
# init-dev.sh - Initialize Providius for local development

set -e

echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║ Providius — Development Initialization                                 ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OPEN_CMD="open"
    SED_CMD="sed -i ''"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OPEN_CMD="xdg-open"
    SED_CMD="sed -i"
else
    OPEN_CMD="echo 'Please open'"
    SED_CMD="sed -i"
fi

# 1. Check prerequisites
echo "✓ Checking prerequisites..."
if ! command -v docker &> /dev/null; then
    echo "✗ Docker not found. Please install Docker: https://docker.com"
    exit 1
fi
if ! command -v node &> /dev/null; then
    echo "✗ Node.js not found. Please install Node.js: https://nodejs.org"
    exit 1
fi
echo "✓ Docker and Node.js found"
echo ""

# 2. Setup environment files
echo "✓ Setting up environment files..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  → Created .env from .env.example"
else
    echo "  → .env already exists"
fi

if [ ! -f providius-dashboard/.env.local ]; then
    cat > providius-dashboard/.env.local << EOF
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_APP_NAME=Providius
NEXT_PUBLIC_ENVIRONMENT=development
EOF
    echo "  → Created providius-dashboard/.env.local"
else
    echo "  → .env.local already exists"
fi
echo ""

# 3. Install frontend dependencies
echo "✓ Installing frontend dependencies..."
cd providius-dashboard
npm install
cd ..
echo ""

# 4. Start Docker services
echo "✓ Starting Docker services..."
docker compose up --build -d
echo ""

# Wait for services to be healthy
echo "⧗ Waiting for services to be ready..."
for i in {1..30}; do
    if curl -f http://localhost:8000/health &> /dev/null; then
        echo "✓ API is ready"
        break
    fi
    if [ $i -lt 30 ]; then
        echo "  Attempt $i/30 - Waiting for API..."
        sleep 2
    else
        echo "✗ API did not respond in time"
        echo "  Check logs: docker compose logs app"
        exit 1
    fi
done
echo ""

# 5. Success message
echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║ ✓ Providius Setup Complete!                                            ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "🚀 Available Services:"
echo ""
echo "  Backend API       http://localhost:8000"
echo "  API Docs          http://localhost:8000/docs"
echo "  Frontend (Next.js) http://localhost:3000 (run: cd providius-dashboard && npm run dev)"
echo "  Prometheus        http://localhost:9090"
echo "  Grafana           http://localhost:3000 (admin/integrateai_grafana)"
echo ""
echo "📚 Next Steps:"
echo "  1. Review configuration: cat .env"
echo "  2. Start frontend: cd providius-dashboard && npm run dev"
echo "  3. View logs: docker compose logs -f app"
echo "  4. Stop all: make down"
echo ""
echo "✓ Run 'make help' for all available commands"
echo ""
