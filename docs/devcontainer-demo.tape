Output docs/images/devcontainer-demo.gif

Require docker
Require jq
Require npx

Set Shell "zsh"
Set FontSize 18
Set Width 1600
Set Height 760
Set Theme "Catppuccin Mocha"
Set TypingSpeed 42ms
Set Padding 24
Set Margin 0
Set MarginFill "#0b1020"

Type "cd $DEVCONTAINER_DEMO_ROOT"
Enter
Sleep 1s

Type "$DEVCONTAINER_DEMO_CONTAINER_BIN system status"
Enter
Wait+Screen@30s /running/
Sleep 2s
Ctrl+L
Sleep 1s

Type "$DEVCONTAINER_DEMO_BIN version"
Enter
Wait+Screen@20s /devcontainer 1.0.0/
Sleep 2s

Type "eval $($DEVCONTAINER_DEMO_BIN context --socket $DEVCONTAINER_DEMO_SOCKET) && docker version | sed -n '1,16p'"
Enter
Wait+Screen@30s /API version: +1[.]53/
Sleep 3s
Ctrl+L
Sleep 1s

Type "npx --yes @devcontainers/cli@$DEVCONTAINER_DEMO_CLI_VERSION up --workspace-folder Examples/hello --log-level info | jq '{outcome, containerId}'"
Enter
Wait+Screen@600s /"outcome": "success"/
Sleep 3s
Ctrl+L
Sleep 1s

Type "npx --yes @devcontainers/cli@$DEVCONTAINER_DEMO_CLI_VERSION exec --workspace-folder Examples/hello sh -lc 'cat hello.txt; cat /tmp/devcontainer-ready; printf architecture:; uname -m'"
Enter
Wait+Screen@120s /architecture:aarch64/
Sleep 4s
Ctrl+L
Sleep 1s

Type "$DEVCONTAINER_DEMO_CONTAINER_BIN list"
Enter
Wait+Screen@30s /devcontainer/
Sleep 4s
Ctrl+L
Sleep 1s

Type "docker ps -aq --filter label=devcontainer.local_folder=$DEVCONTAINER_DEMO_ROOT/Examples/hello | xargs docker rm -f >/dev/null; test -z $(docker ps -aq --filter label=devcontainer.local_folder=$DEVCONTAINER_DEMO_ROOT/Examples/hello) && echo 'clean shutdown: no demo containers remain'"
Enter
Wait+Screen@60s /clean shutdown: no demo containers remain/
Sleep 4s
