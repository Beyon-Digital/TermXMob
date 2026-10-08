# Deliberate host shutdown

Two actual isolated daemon processes prove exact same-origin managed setup, wrong-host denial without shutdown, and explicit current-host administrator acceptance followed by graceful exit. Desktop emits one private stopping event and exits79; plain daemon exits0 and emits no desktop lifecycle event. No existing user host was signaled.

The native parent uses a pending request latch and separate accepted-stop latch. Failure can clear pending without undoing a confirmed stdout event or normal quit. Exit79 independently resolves the stdout-reader/monitor race for a browser-originated Stop host. Explicit Restart Backend clears deliberate stop state. Cargo check with test sources passed; no local delivery binary was built.

This evidence qualifies actual source daemon behavior and native compilation. Installed-app no-respawn behavior still needs the matching signed-artifact OS Accessibility run; it is not inferred from these results.
