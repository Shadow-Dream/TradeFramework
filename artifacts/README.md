# Local artifacts

Generated archives and imported evidence belong below this directory. Their
binary payloads are ignored by Git; source code must not depend on their paths.

`reference-library/` is an independent local Git checkout that previously sat
at the project root. It is preserved verbatim for local research, but it is not
part of the TradeEngine repository or runtime dependency graph.

`upstream-git/` preserves nested upstream Git metadata removed from vendored
source trees so the project repository can track those sources normally.

`remote-imports/` contains explicit source snapshots imported for parity and
reproduction work. Strategy tools may name a particular immutable snapshot;
Engine production code must not discover or fall back to this directory.
