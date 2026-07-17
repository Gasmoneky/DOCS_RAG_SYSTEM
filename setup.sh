#!/bin/bash

# Force root privileges since we are modifying systemd services
if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run this script as root (sudo ./setup.sh)"
  exit 1
fi

# Detect the real user who ran sudo, otherwise default to current user
REAL_USER=${SUDO_USER:-$(whoami)}

if [ "$REAL_USER" = "root" ]; then
    # If we are truly logged in directly as root on a server
    BASE_DIR="/root"
else
    # If we are on a desktop using 'sudo ./setup.sh'
    BASE_DIR="/home/$REAL_USER"
fi

echo "===================================================="
echo " Starting Automated RAG System Installation..."
echo "===================================================="

PROJECT_DIR="$BASE_DIR/DOCS_RAG_SYSTEM"
DOCS_DIR="$BASE_DIR/drogonmd_files"
SCRIPT_PATH="$PROJECT_DIR/auto_update_rag.sh"
SERVICE_PATH="/etc/systemd/system/rag-updater.service"

# Ensure directories are in place
mkdir -p "$PROJECT_DIR"
mkdir -p "$DOCS_DIR"

# 1. Create the background automation worker script (auto_update_rag.sh)
echo "🛠️ Creating background sync script..."
cat << 'EOF' > "$SCRIPT_PATH"
#!/bin/bash

# Fallback dynamic directory locator for systemd contexts
if [ -n "$BASH_SOURCE" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$(pwd)"
fi

# If systemd strips the path context, fall back to default project path safely
if [ -z "$SCRIPT_DIR" ] || [ "$SCRIPT_DIR" = "/" ]; then
    # Auto-fallback to absolute context if systemd loses context
    SCRIPT_DIR="$(dirname "$0")"
    if [ "$SCRIPT_DIR" = "." ] || [ "$SCRIPT_DIR" = "/" ]; then
        # Last line of defense: Check typical root/home user spaces
        if [ -d "/root/DOCS_RAG_SYSTEM" ]; then
            SCRIPT_DIR="/root/DOCS_RAG_SYSTEM"
        else
            SCRIPT_DIR=$(find /home -maxdepth 2 -name "DOCS_RAG_SYSTEM" -type d 2>/dev/null | head -n 1)
        fi
    fi
fi

ENV_FILE="$SCRIPT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "CRITICAL ERROR: .env file not found at $ENV_FILE. Please pull it from GitHub or create your own"
    exit 1
fi

# Load variables from your .env file and strip carriage returns
export $(grep -v '^#' "$ENV_FILE" | xargs)

PROJECT_DIR=$(echo "$PROJECT_DIR" | tr -d '\r')
DOCS_DIR=$(echo "$DOCS_DIR" | tr -d '\r')

# Dynamic relative path converter in memory
if [[ "$PROJECT_DIR" == .* ]]; then
    PROJECT_DIR="$SCRIPT_DIR/${PROJECT_DIR#.}"
    PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
fi

if [[ "$DOCS_DIR" == .* ]]; then
    DOCS_DIR="$SCRIPT_DIR/${DOCS_DIR#.}"
fi

cd "$PROJECT_DIR" || exit 1

while true; do
    echo "========================================="
    echo "Checking for documentation updates: $(date)"
    echo "========================================="

    if [ ! -d "$DOCS_DIR/.git" ]; then
        echo "Cloning repository for the first time..."
        git clone "$DOCS_REPO_URL" "$DOCS_DIR"
        CHANGES_DETECTED=true
    else
        cd "$DOCS_DIR" && git fetch origin main > /dev/null 2>&1
        LOCAL_HASH=$(git rev-parse HEAD)
        REMOTE_HASH=$(git rev-parse origin/main)

        if [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
            echo "Changes detected on remote branch!"
            git reset --hard origin/main
            git pull origin main
            CHANGES_DETECTED=true
        else
            echo "Documentation matches remote HEAD. No updates."
            CHANGES_DETECTED=false
        fi
        cd "$PROJECT_DIR"
    fi

    if [ "$CHANGES_DETECTED" = true ]; then
        echo "STOPPING AND WIPING SYSTEM CACHE..."
        docker compose down
        docker volume rm "$DOCKER_VOLUME_NAME" 2>/dev/null || true
        docker system prune -f --volumes
        echo "Booting containers fresh and executing data ingestion..."
        docker compose up --build -d
        echo "System reloaded successfully."
    fi

    echo "Sleeping for 12 hours..."
    sleep 43200
done
EOF

# Ensure the background worker script is fully executable
chmod +x "$SCRIPT_PATH"
echo " Worker script initialized at $SCRIPT_PATH"

# 2. Create and register the systemd Service file
echo " Registering systemd daemon service..."
cat << EOF > "$SERVICE_PATH"
[Unit]
Description=12-Hour GitHub Ingestion Sync and Complete Docker Cache Purge
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=/bin/bash $SCRIPT_PATH
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 3. Reload, enable, and fire it up instantly
echo "Starting background update environment..."
systemctl daemon-reload
systemctl enable rag-updater.service
systemctl start rag-updater.service

echo "===================================================="
echo "SUCCESS: Automated syncing service has been set up!"
echo "===================================================="
echo " To see your active syncing logs, run:"
echo " journalctl -u rag-updater.service -f"
echo "===================================================="
