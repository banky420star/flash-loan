# Ensure Foundry tools are resolvable even when the launching shell has no
# PATH entry for the default install location (tmux, systemd, cron).
if ! command -v forge >/dev/null 2>&1; then
  if [ -x "$HOME/.foundry/bin/forge" ]; then
    export PATH="$HOME/.foundry/bin:$PATH"
  fi
fi