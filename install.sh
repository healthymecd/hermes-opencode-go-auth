#!/bin/sh
# hermes-opencode-go installer — copies the model-provider plugin into your Hermes home.
# Usage:
#   ./install.sh                 (installs into $HERMES_HOME or ~/.hermes)
#   HERMES_HOME=/path ./install.sh
set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)/opencode-go"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HERMES_HOME/plugins/model-providers/opencode-go"

if [ ! -f "$SRC_DIR/__init__.py" ]; then
    echo "error: cannot find the plugin directory next to install.sh" >&2
    exit 1
fi

mkdir -p "$HERMES_HOME/plugins/model-providers"

if [ -d "$DEST" ]; then
    echo "An existing opencode-go plugin is installed — replacing it."
    BACKUP="$HERMES_HOME/plugins/model-providers/.opencode-go.bak-$(date +%s)"
    mv "$DEST" "$BACKUP"
    echo "  (previous version moved to $BACKUP)"
fi

cp -R "$SRC_DIR" "$DEST"

echo
echo "✓ Installed opencode-go into $DEST"
echo
echo "Next steps:"
echo "  1. Sign in:        hermes auth add opencode-go"
echo "     (a browser opens; enter the printed code at console.opencode.ai/device)"
echo "  2. Chat with it:   hermes chat --provider opencode-go -m glm-5.3-flash"
echo "  3. Status:         hermes auth status opencode-go"
echo
echo "Static key alternative: set OPENCODE_GO_API_KEY in ~/.hermes/.env"
echo "(device sign-in and static keys are independent — use either)."
